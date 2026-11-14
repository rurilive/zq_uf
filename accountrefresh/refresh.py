"""按账号单飞 + 提前刷新 + 指数退避 + 账号隔离的令牌刷新。"""

import threading
import time

REFRESH_AHEAD = 30.0      # 距过期不足该秒数就提前刷新
BACKOFF_BASE = 1.0        # 首次失败退避秒数
BACKOFF_FACTOR = 2.0      # 退避指数因子
BACKOFF_MAX = 60.0        # 单次退避上限
ISOLATE_AFTER = 3         # 连续失败达到该次数后隔离账号
ISOLATION_FACTOR = 4.0    # 隔离账号退避倍率（降低调度权重）

_lock = threading.Lock()
_cond = threading.Condition(_lock)
_states = {}      # account -> {"token","expires_at","failures","next_try","isolated"}
_inflight = {}    # account -> True 表示该账号有刷新在进行
_counts = {}      # account -> 刷新尝试次数
_reused = {}      # account -> 等待复用次数
_audit = []       # 审计记录
_seq = 0
_token_ttl = 0.0          # 默认令牌立即过期，保持逐次刷新行为
_token_source = None      # 可注入令牌来源：fn(account) -> (token, expires_in)
_now = time.monotonic


def _new_state():
    return {"token": None, "expires_at": 0.0, "failures": 0,
            "next_try": None, "isolated": False}


def _log(account, action, detail=""):
    _audit.append({"ts": time.time(), "account": account,
                   "action": action, "detail": detail})


def _issue(account):
    global _seq
    if _token_source is not None:
        return _token_source(account)
    with _lock:
        _seq += 1
        seq = _seq
    return "v" + str(seq), _token_ttl


def refresh(account, force=False):
    """按账号单飞刷新；force 跳过缓存与退避直接刷。

    同一账号同时只有一个刷新在跑，其余等待并复用结果；
    账号之间各自持锁，互不阻塞。
    """
    waited = False
    while True:
        with _cond:
            state = _states.setdefault(account, _new_state())
            now = _now()
            if (not force and state["token"] is not None
                    and state["expires_at"] - now > REFRESH_AHEAD):
                _log(account, "cache_hit")
                return {"account": account, "token": state["token"],
                        "expires_at": state["expires_at"],
                        "refreshed": False, "reused": waited}
            if (not force and state["next_try"] is not None
                    and now < state["next_try"]):
                _log(account, "backoff_skip")
                return {"account": account, "token": None, "refreshed": False,
                        "backoff": True, "isolated": state["isolated"],
                        "next_try": state["next_try"]}
            if _inflight.get(account):
                _reused[account] = _reused.get(account, 0) + 1
                _log(account, "reuse_wait")
                while _inflight.get(account):
                    _cond.wait()
                waited = True
                force = False  # 等待后复用结果，不再自己发起
                continue
            _inflight[account] = True
            _counts[account] = _counts.get(account, 0) + 1
            break
    try:
        token, expires_in = _issue(account)
        error = None
    except Exception as exc:  # 令牌来源失败进入退避
        token, expires_in, error = None, 0.0, exc
    with _cond:
        state = _states[account]
        now = _now()
        if error is None:
            state.update(token=token, expires_at=now + expires_in,
                         failures=0, next_try=None, isolated=False)
            _log(account, "refresh_success")
            result = {"account": account, "token": token,
                      "expires_at": state["expires_at"], "refreshed": True}
        else:
            state["failures"] += 1
            if state["failures"] >= ISOLATE_AFTER:
                state["isolated"] = True
            delay = min(BACKOFF_MAX,
                        BACKOFF_BASE * BACKOFF_FACTOR ** (state["failures"] - 1))
            if state["isolated"]:
                delay = min(BACKOFF_MAX * ISOLATION_FACTOR,
                            delay * ISOLATION_FACTOR)
            state["next_try"] = now + delay
            _log(account, "refresh_failure", str(error))
            result = {"account": account, "token": None, "refreshed": False,
                      "error": str(error), "isolated": state["isolated"],
                      "next_try": state["next_try"]}
        _inflight.pop(account, None)
        _cond.notify_all()
        return result


def get_token(account):
    """取令牌；临近过期才刷新，并发复用同一次刷新。"""
    return refresh(account)


def stats():
    with _lock:
        return dict(_counts)


def report():
    """刷新次数、等待复用数、退避状态和隔离账号。"""
    with _lock:
        now = _now()
        return {
            "refreshes": dict(_counts),
            "reused": dict(_reused),
            "backoff": {a: s["next_try"] for a, s in _states.items()
                        if s["next_try"] is not None and s["next_try"] > now},
            "isolated": sorted(a for a, s in _states.items() if s["isolated"]),
        }


def audit_log():
    """审计记录副本，含每次刷新/复用/退避跳过。"""
    with _lock:
        return [dict(row) for row in _audit]
