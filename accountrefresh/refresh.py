"""账号令牌刷新：按账号单飞、提前刷新、指数退避与隔离。"""

import threading
import time

REFRESH_AHEAD = 30.0
TOKEN_TTL = 0.0
BACKOFF_BASE = 1.0
BACKOFF_MAX = 60.0
ISOLATE_THRESHOLD = 3
ISOLATED_WEIGHT = 0.1

_counts = {}
_seq = 0

_tokens = {}
_backoff = {}
_inflight = {}
_conds = {}
_wait_reused = {}
_isolated = set()
_audit = []

_state_lock = threading.Lock()


class BackoffError(RuntimeError):
    """账号处于退避期，next_try 之前不再请求令牌源。"""

    def __init__(self, account, next_try):
        super().__init__("account %s in backoff until %.3f" % (account, next_try))
        self.account = account
        self.next_try = next_try


def _default_source(account):
    global _seq
    _seq += 1
    return {"token": "v" + str(_seq), "expires_in": TOKEN_TTL}


_token_source = _default_source


def _reset():
    global _seq
    with _state_lock:
        _counts.clear()
        _tokens.clear()
        _backoff.clear()
        _inflight.clear()
        _wait_reused.clear()
        _isolated.clear()
        del _audit[:]
        _seq = 0


def _cond_for(account):
    cond = _conds.get(account)
    if cond is None:
        cond = _conds[account] = threading.Condition(_state_lock)
    return cond


def _record_failure(account):
    now = time.time()
    bo = _backoff.get(account)
    failures = (bo["failures"] if bo else 0) + 1
    delay = min(BACKOFF_BASE * (2 ** (failures - 1)), BACKOFF_MAX)
    next_try = now + delay
    _backoff[account] = {"failures": failures, "next_try": next_try}
    _audit.append({"ts": now, "account": account, "event": "refresh_failure",
                   "failures": failures, "next_try": next_try})
    if failures >= ISOLATE_THRESHOLD and account not in _isolated:
        _isolated.add(account)
        _audit.append({"ts": now, "account": account, "event": "isolated"})


def _acquire(account, force):
    waited = False
    while True:
        with _state_lock:
            now = time.time()
            entry = _tokens.get(account)
            if not force and entry is not None and entry["expires_at"] - now > REFRESH_AHEAD:
                if waited:
                    _wait_reused[account] = _wait_reused.get(account, 0) + 1
                return {"account": account, "token": entry["token"], "refreshed": False}
            bo = _backoff.get(account)
            if bo is not None and now < bo["next_try"]:
                raise BackoffError(account, bo["next_try"])
            if _inflight.get(account):
                waited = True
                _cond_for(account).wait()
                continue
            _inflight[account] = True
        try:
            issued = _token_source(account)
            token = issued["token"]
            expires_in = float(issued.get("expires_in", TOKEN_TTL))
        except Exception:
            with _state_lock:
                _record_failure(account)
                _inflight[account] = False
                _cond_for(account).notify_all()
            raise
        with _state_lock:
            _tokens[account] = {"token": token, "expires_at": time.time() + expires_in}
            _counts[account] = _counts.get(account, 0) + 1
            _backoff.pop(account, None)
            _isolated.discard(account)
            _audit.append({"ts": time.time(), "account": account,
                           "event": "refresh_success", "token": token})
            _inflight[account] = False
            _cond_for(account).notify_all()
            return {"account": account, "token": token, "refreshed": True}


def get_token(account):
    """取令牌：缓存未临期直接复用，否则按账号单飞刷新。"""
    return _acquire(account, force=False)


def refresh(account, force=False):
    """刷新令牌；force=True 跳过缓存判断强制刷新。"""
    return _acquire(account, force=force)


def stats():
    """刷新次数、等待复用数、退避状态、隔离账号与调度权重。"""
    with _state_lock:
        result = dict(_counts)
        result["wait_reused"] = dict(_wait_reused)
        result["backoff"] = {a: dict(b) for a, b in _backoff.items()}
        result["isolated"] = sorted(_isolated)
        seen = set(_counts) | set(_tokens) | set(_backoff)
        result["weights"] = {a: (ISOLATED_WEIGHT if a in _isolated else 1.0)
                             for a in sorted(seen)}
        return result


def audit_log():
    """刷新审计记录（成功、失败、隔离）。"""
    with _state_lock:
        return [dict(r) for r in _audit]
