"""采集并发爬升：按站点成功率阶梯调并发，并发占用与站点配额联动。

阶段：
- ramp：连续成功 _RAMP_AFTER 次升一档（翻倍，上限 _MAX_LIMIT）。
- cooldown：连续失败 _BACKOFF_AFTER 次回退一档（减半，下限 _MIN_LIMIT）并冷却。

口径：
- acquire 成功 = 占 1 路并发租约 + 扣 1 站点配额（令牌桶）。
- 配额耗尽不抛错，返回 reason=quota 与可等待时间 wait，并记排队位置。
- 并发租约 _LEASE_TTL 秒，慢站超时占用被回收（reclaim），让出额度。
- report 只记录结果并驱动爬升/回退，不释放租约。
- 同一站点的阶段状态保存在模块级字典里，跨任务共享。
"""

import logging
import threading
import time

logger = logging.getLogger("crawlcap")

_BASE_LIMIT = 4
_MIN_LIMIT = 1
_MAX_LIMIT = 16
_RAMP_AFTER = 4       # 连续成功多少次升一档
_BACKOFF_AFTER = 3    # 连续失败多少次回退并冷却
_COOLDOWN_SECS = 5.0  # 回退后的冷却时长
_LEASE_TTL = 30.0     # 并发租约时长，超时视为慢站占用并回收
_SLOW_RTT = 2.0       # 超过该 rtt 记一条 slow-rtt 日志
_QUOTA_CAPACITY = 64  # 站点配额令牌桶容量
_QUOTA_RATE = 1.0     # 每秒回补的配额

_lock = threading.RLock()
_sites = {}


def _now():
    return time.monotonic()


def _site(site):
    st = _sites.get(site)
    if st is None:
        st = {
            "phase": "ramp",
            "limit": _BASE_LIMIT,
            "holders": [],          # 每路并发租约的到期时间
            "tokens": float(_QUOTA_CAPACITY),
            "token_ts": _now(),
            "ok_streak": 0,
            "fail_streak": 0,
            "cooldown_until": 0.0,
            "queued": 0,            # 因配额不足排队的请求数
            "backoffs": [],         # 回退记录
        }
        _sites[site] = st
    return st


def reset(site=None):
    """清空站点状态（测试/演示用）；不传站点则全部清空。"""
    with _lock:
        if site is None:
            _sites.clear()
        else:
            _sites.pop(site, None)


def _refill(st, now):
    elapsed = now - st["token_ts"]
    if elapsed > 0:
        st["tokens"] = min(float(_QUOTA_CAPACITY), st["tokens"] + elapsed * _QUOTA_RATE)
        st["token_ts"] = now


def _reap(site, st, now):
    alive = [d for d in st["holders"] if d > now]
    freed = len(st["holders"]) - len(alive)
    if freed:
        st["holders"] = alive
        logger.info("reclaim site=%s freed=%d reason=slow-lease running=%d",
                    site, freed, len(alive))


def _view(site, st, **extra):
    view = {
        "site": site,
        "phase": st["phase"],
        "concurrency": len(st["holders"]),
        "limit": st["limit"],
        "quota": int(st["tokens"]),
    }
    view.update(extra)
    return view


def acquire(site):
    """申请一路并发；成功同时扣站点配额，失败返回原因与可等待时间。"""
    with _lock:
        now = _now()
        st = _site(site)
        _refill(st, now)
        _reap(site, st, now)

        if st["phase"] == "cooldown":
            wait = st["cooldown_until"] - now
            if wait > 0:
                return _view(site, st, ok=False, reason="cooldown", wait=wait)
            st["phase"] = "ramp"
            logger.info("cooldown-end site=%s limit=%d", site, st["limit"])

        if len(st["holders"]) >= st["limit"]:
            wait = max(0.0, min(st["holders"]) - now) if st["holders"] else 0.0
            return _view(site, st, ok=False, reason="full", wait=wait)

        if st["tokens"] < 1.0:
            st["queued"] += 1
            wait = (1.0 - st["tokens"]) / _QUOTA_RATE
            logger.info("queue site=%s position=%d wait=%.2f", site, st["queued"], wait)
            return _view(site, st, ok=False, reason="quota", wait=wait,
                         queue=st["queued"])

        st["tokens"] -= 1.0
        st["holders"].append(now + _LEASE_TTL)
        if st["queued"]:
            st["queued"] -= 1
        logger.info("grant site=%s concurrency=%d limit=%d quota=%d",
                    site, len(st["holders"]), st["limit"], int(st["tokens"]))
        return _view(site, st, ok=True)


def report(site, ok, rtt):
    """记录一次请求结果，按成功率驱动爬升/回退；不释放并发租约。"""
    with _lock:
        now = _now()
        st = _site(site)
        _refill(st, now)
        event = None

        if rtt is not None and rtt > _SLOW_RTT:
            logger.info("slow-rtt site=%s rtt=%.2f", site, rtt)

        if ok:
            st["ok_streak"] += 1
            st["fail_streak"] = 0
            if (st["phase"] == "ramp" and st["ok_streak"] >= _RAMP_AFTER
                    and st["limit"] < _MAX_LIMIT):
                old = st["limit"]
                st["limit"] = min(_MAX_LIMIT, st["limit"] * 2)
                st["ok_streak"] = 0
                event = "ramp"
                logger.info("ramp site=%s limit=%d->%d", site, old, st["limit"])
        else:
            st["ok_streak"] = 0
            st["fail_streak"] += 1
            if st["fail_streak"] >= _BACKOFF_AFTER:
                old = st["limit"]
                st["limit"] = max(_MIN_LIMIT, st["limit"] // 2)
                st["fail_streak"] = 0
                st["phase"] = "cooldown"
                st["cooldown_until"] = now + _COOLDOWN_SECS
                st["backoffs"].append({
                    "at": now, "from": old, "to": st["limit"],
                    "cooldown": _COOLDOWN_SECS,
                })
                event = "backoff"
                logger.info("backoff site=%s limit=%d->%d cooldown=%.1fs",
                            site, old, st["limit"], _COOLDOWN_SECS)

        return _view(site, st, ok=ok, rtt=rtt, event=event,
                     backoff=st["backoffs"][-1] if st["backoffs"] else None)
