"""带版本和容量的抽取规则编译缓存。

口径：
- 缓存键 = 规则指纹 sha256(规则 id + version + params)，规则变更即指纹变更。
- 未声明 version 的规则不参与缓存，每次重新编译（兼容旧行为）。
- 容量按条数限制，淘汰最久未用：last_access + 命中次数 * 60s 最低者先出。
- 多进程共享：缓存落盘，fcntl 文件锁保护，临时文件 + os.replace 原子替换。
- 失效广播：invalidate 把版本写入共享 invalidations 日志，其他进程读取时懒清理。
- 编译失败（规则非法）不写缓存。
"""

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import time

_DEFAULT_CAPACITY = 128
_HIT_BONUS_SECONDS = 60.0  # 每次命中折算的“新近度”奖励，用于淘汰打分
_UNVERSIONED = "v1"  # 未带版本规则返回的兼容版本号

_ENTRIES = "entries.json"
_INVALIDATIONS = "invalidations.json"
_STATS = "stats.json"
_LOCK = "cache.lock"


def _home():
    return os.environ.get("EXTRACTCACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".extractcache"
    )


def _capacity():
    try:
        return max(1, int(os.environ.get("EXTRACTCACHE_CAPACITY", _DEFAULT_CAPACITY)))
    except ValueError:
        return _DEFAULT_CAPACITY


@contextlib.contextmanager
def _locked():
    """跨进程文件锁，保护一次读-改-写。"""
    home = _home()
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, _LOCK), "a+b") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield home
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _load(home, name, default):
    try:
        with open(os.path.join(home, name), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _store(home, name, data):
    """先写临时文件再原子替换，避免其他进程读到半截文件。"""
    fd, tmp = tempfile.mkstemp(dir=home, prefix=name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, os.path.join(home, name))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _new_stats():
    return {
        "hits": 0,
        "misses": 0,
        "invalidations": 0,
        "evictions": 0,
        "compiled": 0,
        "rules": {},  # 按规则 id 统计命中/未命中
    }


def _rule_stat(stats, rule_id, hit):
    rs = stats["rules"].setdefault(rule_id, {"hits": 0, "misses": 0})
    rs["hits" if hit else "misses"] += 1


def _normalize(rules):
    """规整规则；非法规则抛 ValueError，视为编译失败。"""
    normalized = []
    for rule in rules:
        if isinstance(rule, str):
            normalized.append({"id": rule, "version": None, "params": {}})
        elif isinstance(rule, dict):
            rid = rule.get("id", rule.get("name"))
            if not isinstance(rid, str) or not rid:
                raise ValueError("规则缺少 id: %r" % (rule,))
            params = rule.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("规则参数必须是 dict: %r" % (rule,))
            version = rule.get("version")
            if version is not None and not isinstance(version, str):
                raise ValueError("规则版本必须是 str: %r" % (rule,))
            normalized.append({"id": rid, "version": version, "params": params})
        else:
            raise ValueError("无法编译的规则: %r" % (rule,))
    return normalized


def _fingerprint(normalized):
    blob = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _evict(entries, capacity):
    """按 访问时间 + 命中次数加权 淘汰最久未用，返回淘汰条数。"""
    evicted = 0
    while len(entries) > capacity:
        victim = min(
            entries,
            key=lambda k: entries[k]["last_access"]
            + entries[k]["hits"] * _HIT_BONUS_SECONDS,
        )
        del entries[victim]
        evicted += 1
    return evicted


def _purge_invalidated(entries, invalidated):
    """懒清理：其他进程广播过的失效版本，本地读到即删。"""
    stale = [
        k
        for k, e in entries.items()
        if any(v in invalidated for v in e["versions"])
    ]
    for k in stale:
        del entries[k]
    return len(stale)


def compile(rules):
    """编译规则；带版本的规则按指纹缓存，命中则直接返回缓存结果。"""
    normalized = _normalize(rules)  # 非法规则在此抛出，不写缓存
    versioned = bool(normalized) and all(r["version"] for r in normalized)
    with _locked() as home:
        stats = _load(home, _STATS, _new_stats())
        if not versioned:
            stats["compiled"] += 1
            _store(home, _STATS, stats)
            return {
                "rules": list(rules),
                "compiled": stats["compiled"],
                "cached": False,
                "version": _UNVERSIONED,
            }

        key = _fingerprint(normalized)
        versions = sorted({r["version"] for r in normalized})
        entries = _load(home, _ENTRIES, {})
        invalidated = _load(home, _INVALIDATIONS, {})
        _purge_invalidated(entries, invalidated)

        entry = entries.get(key)
        if entry is not None:
            entry["hits"] += 1
            entry["last_access"] = time.time()
            stats["hits"] += 1
            for r in normalized:
                _rule_stat(stats, r["id"], hit=True)
            _store(home, _ENTRIES, entries)
            _store(home, _STATS, stats)
            result = dict(entry["result"])
            result["compiled"] = stats["compiled"]
            result["cached"] = True
            return result

        stats["misses"] += 1
        stats["compiled"] += 1
        for r in normalized:
            _rule_stat(stats, r["id"], hit=False)
        result = {
            "rules": list(rules),
            "compiled": stats["compiled"],
            "cached": False,
            "version": versions[0] if len(versions) == 1 else versions,
        }
        entries[key] = {
            "result": result,
            "versions": versions,
            "hits": 0,
            "last_access": time.time(),
            "created": time.time(),
            "rule_ids": [r["id"] for r in normalized],
        }
        stats["evictions"] += _evict(entries, _capacity())
        _store(home, _ENTRIES, entries)
        _store(home, _STATS, stats)
        return dict(result)


def invalidate(version):
    """使某个版本的全部缓存失效，并广播给其他进程。"""
    with _locked() as home:
        entries = _load(home, _ENTRIES, {})
        invalidated = _load(home, _INVALIDATIONS, {})
        stats = _load(home, _STATS, _new_stats())
        doomed = [k for k, e in entries.items() if version in e["versions"]]
        for k in doomed:
            del entries[k]
        invalidated[version] = time.time()  # 广播：其他进程下次读取时懒清理
        stats["invalidations"] += len(doomed)
        _store(home, _ENTRIES, entries)
        _store(home, _INVALIDATIONS, invalidated)
        _store(home, _STATS, stats)
        return {"version": version, "invalidated": len(doomed)}


def stats():
    """输出命中、失效、淘汰和命中率（命中率按规则统计）。"""
    with _locked() as home:
        s = _load(home, _STATS, _new_stats())
        entries = _load(home, _ENTRIES, {})
    hits, misses = s["hits"], s["misses"]
    total = hits + misses
    per_rule = {}
    for rid, rs in s["rules"].items():
        rt = rs["hits"] + rs["misses"]
        per_rule[rid] = {
            "hits": rs["hits"],
            "misses": rs["misses"],
            "hit_rate": rs["hits"] / rt if rt else 0.0,
        }
    return {
        "hits": hits,
        "misses": misses,
        "invalidations": s["invalidations"],
        "evictions": s["evictions"],
        "compiled": s["compiled"],
        "hit_rate": hits / total if total else 0.0,
        "size": len(entries),
        "capacity": _capacity(),
        "per_rule": per_rule,
    }
