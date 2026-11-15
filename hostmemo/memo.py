"""站点能力记忆：按站点记忆请求头、编码与成功特征，带版本、过期与失效。

记忆口径：
- remember(host, profile) 保存画像（headers / encoding / features），
  版本号 +1，写入过期时间；profile 里带 features（成功特征）的记忆才参与命中。
- get(host) 只在记忆未过期且带成功特征时命中（cached=True），
  否则返回默认画像，并用 probe / retry_after 提示是否该重探。

失效口径：
- 过期、探测失败（report_failure）、成功特征变化（report_features）都会让记忆失效；
  失效后第一次 get 会记录一条 reprobe 事件并提示重探。
- 探测失败按 2**n 秒退避，退避期间 get 返回 probe=False 和 retry_after。
- 存储文件损坏会被挪为 .corrupt 并当作空库重探，异常记忆不会长期生效。

多进程共享：
- 记忆落在一个 JSON 文件里，写操作 flock 加锁、临时文件 + os.replace 原子替换；
  remember(..., expected_version=...) 用版本号检测并发写冲突。
"""

from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import os
import time

DEFAULT_TTL = 3600.0        # 默认记忆有效期（秒）
MAX_TTL = 24 * 3600.0       # 单条记忆最长有效期，异常记忆不能长期生效
BACKOFF_BASE = 1.0          # 探测失败退避基数（秒）
BACKOFF_CAP = 300.0         # 退避上限（秒）
EVENT_LIMIT = 200           # 事件记录上限，超出丢弃最旧的

_DEFAULT_PROFILE = {"headers": {}, "encoding": "utf-8"}

_STORE_PATH = os.environ.get(
    "HOSTMEMO_STORE",
    os.path.join(os.path.expanduser("~"), ".cache", "hostmemo", "store.json"),
)
_TTL = DEFAULT_TTL
_BACKOFF_BASE = BACKOFF_BASE


def configure(store_path=None, ttl=None, backoff_base=None):
    """调整存储位置与参数，返回当前生效的配置（可再传回 configure 恢复）。"""
    global _STORE_PATH, _TTL, _BACKOFF_BASE
    if store_path is not None:
        _STORE_PATH = store_path
    if ttl is not None:
        _TTL = min(float(ttl), MAX_TTL)
    if backoff_base is not None:
        _BACKOFF_BASE = float(backoff_base)
    return {"store_path": _STORE_PATH, "ttl": _TTL, "backoff_base": _BACKOFF_BASE}


def _empty_db():
    return {"entries": {}, "probes": {}, "stats": {}, "events": []}


def _load():
    try:
        with open(_STORE_PATH, "r", encoding="utf-8") as fh:
            db = json.load(fh)
    except FileNotFoundError:
        return _empty_db()
    except (OSError, ValueError):
        # 损坏的记忆不能长期生效：挪到一边，按空库重新探测
        try:
            os.replace(_STORE_PATH, _STORE_PATH + ".corrupt")
        except OSError:
            pass
        return _empty_db()
    empty = _empty_db()
    for key in empty:
        db.setdefault(key, empty[key])
    return db


def _save(db):
    tmp = "%s.tmp.%d" % (_STORE_PATH, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(db, fh, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, _STORE_PATH)


@contextlib.contextmanager
def _locked_db():
    os.makedirs(os.path.dirname(os.path.abspath(_STORE_PATH)), exist_ok=True)
    with open(_STORE_PATH + ".lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        db = _load()
        yield db
        _save(db)


def _record(db, event, host, **detail):
    db["events"].append({"event": event, "host": host, "at": time.time(), **detail})
    del db["events"][:-EVENT_LIMIT]


def _probe_state(db, host):
    return db["probes"].setdefault(
        host, {"failures": 0, "backoff_until": 0.0, "pending_reprobe": False}
    )


def remember(host, profile, ttl=None, expected_version=None):
    """写入站点画像，版本号 +1 并设置过期时间。

    expected_version 用于多进程写冲突检测：与当前版本不一致时不写入，
    返回 {"stored": False, "conflict": True}。
    """
    now = time.time()
    with _locked_db() as db:
        current = db["entries"].get(host)
        cur_version = current["version"] if current else 0
        if expected_version is not None and cur_version != expected_version:
            _record(db, "conflict", host, version=cur_version)
            return {
                "host": host,
                "stored": False,
                "conflict": True,
                "version": cur_version,
            }
        old_features = (current or {}).get("profile", {}).get("features")
        new_features = dict(profile).get("features")
        if current and old_features is not None and old_features != new_features:
            _record(db, "invalidate", host, reason="features-changed")
        effective_ttl = min(float(ttl) if ttl is not None else _TTL, MAX_TTL)
        db["entries"][host] = {
            "host": host,
            "profile": copy.deepcopy(dict(profile)),
            "version": cur_version + 1,
            "created_at": now,
            "expires_at": now + effective_ttl,
        }
        # 探测成功：重置失败计数与退避
        db["probes"][host] = {
            "failures": 0,
            "backoff_until": 0.0,
            "pending_reprobe": False,
        }
        return {
            "host": host,
            "stored": True,
            "version": cur_version + 1,
            "expires_at": now + effective_ttl,
        }


def get(host):
    """读取站点记忆。

    命中：cached=True，profile 为记忆内容，probe=False。
    未命中：cached=False，profile 为默认画像；probe=True 表示应重探，
    退避期间 probe=False 且 retry_after 给出剩余等待秒数。
    """
    now = time.time()
    with _locked_db() as db:
        stats = db["stats"].setdefault(host, {"hits": 0, "misses": 0})
        probe_state = _probe_state(db, host)
        result = {
            "host": host,
            "profile": copy.deepcopy(_DEFAULT_PROFILE),
            "cached": False,
            "probe": True,
            "retry_after": 0.0,
            "version": 0,
            "reason": "miss",
        }

        entry = db["entries"].get(host)
        if entry is not None and entry["expires_at"] <= now:
            del db["entries"][host]
            probe_state["pending_reprobe"] = True
            _record(db, "invalidate", host, reason="expired")
            entry = None
            result["reason"] = "expired"

        if entry is not None and entry["profile"].get("features") is not None:
            stats["hits"] += 1
            _record(db, "hit", host, version=entry["version"])
            result.update(
                profile=copy.deepcopy(entry["profile"]),
                cached=True,
                probe=False,
                version=entry["version"],
                reason="hit",
            )
            return result

        if entry is not None:
            # 没有成功特征的记忆视为未验证，不参与命中
            result["reason"] = "unverified"
            result["version"] = entry["version"]

        stats["misses"] += 1
        backoff_until = probe_state["backoff_until"]
        if backoff_until > now:
            result["probe"] = False
            result["retry_after"] = backoff_until - now
            result["reason"] = "backoff"
        else:
            _record(db, "miss", host, reason=result["reason"])
            if probe_state["pending_reprobe"]:
                probe_state["pending_reprobe"] = False
                _record(db, "reprobe", host)
        return result


def report_failure(host):
    """探测失败：让现有记忆失效，并按 2**n 秒退避重试。"""
    now = time.time()
    with _locked_db() as db:
        if host in db["entries"]:
            del db["entries"][host]
            _record(db, "invalidate", host, reason="probe-failed")
        probe_state = _probe_state(db, host)
        probe_state["failures"] += 1
        delay = min(
            _BACKOFF_BASE * (2 ** (probe_state["failures"] - 1)), BACKOFF_CAP
        )
        probe_state["backoff_until"] = now + delay
        probe_state["pending_reprobe"] = True
        _record(
            db, "backoff", host,
            failures=probe_state["failures"], retry_after=delay,
        )
        return {
            "host": host,
            "failures": probe_state["failures"],
            "retry_after": delay,
        }


def report_features(host, features):
    """上报最新成功特征；与记忆不一致时让记忆失效，等待重探。"""
    with _locked_db() as db:
        entry = db["entries"].get(host)
        current = entry["profile"].get("features") if entry else None
        changed = current is not None and current != features
        if changed:
            del db["entries"][host]
            _probe_state(db, host)["pending_reprobe"] = True
            _record(db, "invalidate", host, reason="features-changed")
        return {"host": host, "changed": changed}


def events(host=None):
    """命中、失效、重探等事件记录，可按站点过滤。"""
    with _locked_db() as db:
        items = db["events"]
        if host is not None:
            items = [e for e in items if e["host"] == host]
        return copy.deepcopy(items)


def stats(host=None):
    """按站点统计命中次数与命中率；不传 host 返回全部站点。"""
    with _locked_db() as db:
        all_stats = db["stats"]

        def fmt(name, item):
            total = item["hits"] + item["misses"]
            return {
                "host": name,
                "hits": item["hits"],
                "misses": item["misses"],
                "hit_rate": (item["hits"] / total) if total else 0.0,
            }

        if host is not None:
            return fmt(host, all_stats.get(host, {"hits": 0, "misses": 0}))
        return {name: fmt(name, item) for name, item in sorted(all_stats.items())}


def clear(host=None):
    """人工清理记忆：传 host 清理单站，不传清空全部。"""
    with _locked_db() as db:
        if host is None:
            removed = len(db["entries"])
            db["entries"].clear()
            db["probes"].clear()
            _record(db, "clear", "*", removed=removed)
            return {"cleared": removed}
        removed = 1 if db["entries"].pop(host, None) is not None else 0
        db["probes"].pop(host, None)
        _record(db, "clear", host, removed=removed)
        return {"host": host, "cleared": removed}
