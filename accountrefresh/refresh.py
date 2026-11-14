"""账号令牌刷新：按账号单飞、提前刷新、指数退避与隔离。"""

import json
import threading
import time

_DEFAULT_REFRESH_AHEAD = 30.0
_DEFAULT_BACKOFF_BASE = 1.0
_DEFAULT_BACKOFF_MAX = 60.0
_DEFAULT_ISOLATE_THRESHOLD = 3
_DEFAULT_TOKEN_TTL = 0.0

_REFRESH_AHEAD = _DEFAULT_REFRESH_AHEAD
_BACKOFF_BASE = _DEFAULT_BACKOFF_BASE
_BACKOFF_MAX = _DEFAULT_BACKOFF_MAX
_ISOLATE_THRESHOLD = _DEFAULT_ISOLATE_THRESHOLD
_TOKEN_TTL = _DEFAULT_TOKEN_TTL

_counts = {}
_seq = 0

_states = {}
_states_lock = threading.Lock()

_audit_records = []
_audit_lock = threading.Lock()
_audit_path = None


def _default_issuer(account):
    global _seq
    _seq += 1
    return {"token": "v" + str(_seq), "expires_in": _TOKEN_TTL}


_issuer = _default_issuer
_clock = time.monotonic


class _AccountState:
    __slots__ = ("lock", "token", "expires_at", "failures", "next_try",
                 "isolated", "reused")

    def __init__(self):
        self.lock = threading.Lock()
        self.token = None
        self.expires_at = 0.0
        self.failures = 0
        self.next_try = 0.0
        self.isolated = False
        self.reused = 0


def _state_for(account):
    with _states_lock:
        state = _states.get(account)
        if state is None:
            state = _states[account] = _AccountState()
        return state


def _fresh(state, now):
    return state.token is not None and now < state.expires_at - _REFRESH_AHEAD


def _valid(state, now):
    return state.token is not None and now < state.expires_at


def _weight(state):
    if state.failures == 0:
        return 1.0
    return max(0.05, 0.5 ** state.failures)


def _audit(event, account, **detail):
    record = {"ts": _clock(), "event": event, "account": account}
    record.update(detail)
    with _audit_lock:
        _audit_records.append(record)
        if _audit_path is not None:
            with open(_audit_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _refresh_locked(account, state, now, force):
    if not force and now < state.next_try:
        _audit("backoff", account, next_try=state.next_try)
        token = state.token if _valid(state, now) else None
        return {"account": account, "token": token, "refreshed": False,
                "error": "backoff", "next_try": state.next_try}
    try:
        issued = _issuer(account)
    except Exception as exc:
        state.failures += 1
        delay = min(_BACKOFF_BASE * (2 ** (state.failures - 1)), _BACKOFF_MAX)
        state.next_try = _clock() + delay
        if state.failures >= _ISOLATE_THRESHOLD and not state.isolated:
            state.isolated = True
            _audit("isolated", account, failures=state.failures)
        _audit("refresh_failure", account, error=str(exc),
               next_try=state.next_try)
        token = state.token if _valid(state, now) else None
        return {"account": account, "token": token, "refreshed": False,
                "error": str(exc), "next_try": state.next_try}
    state.token = issued["token"]
    state.expires_at = _clock() + float(issued.get("expires_in", 0))
    if state.isolated:
        _audit("recovered", account)
    state.failures = 0
    state.next_try = 0.0
    state.isolated = False
    _counts[account] = _counts.get(account, 0) + 1
    _audit("refresh_success", account, token=state.token)
    return {"account": account, "token": state.token, "refreshed": True}


def get_token(account):
    """令牌新鲜直接复用；进入提前量或已过期则按账号单飞刷新。"""
    state = _state_for(account)
    if _fresh(state, _clock()):
        return {"account": account, "token": state.token, "refreshed": False}
    with state.lock:
        now = _clock()
        if _fresh(state, now):
            state.reused += 1
            _audit("reuse", account)
            return {"account": account, "token": state.token,
                    "refreshed": False, "reused": True}
        return _refresh_locked(account, state, now, force=False)


def refresh(account, force=False):
    """手动刷新；force=True 跳过缓存和退避直接找签发方。"""
    state = _state_for(account)
    with state.lock:
        now = _clock()
        if not force and _fresh(state, now):
            return {"account": account, "token": state.token,
                    "refreshed": False}
        return _refresh_locked(account, state, now, force=force)


def stats():
    """各账号成功刷新次数。"""
    return dict(_counts)


def status():
    """刷新次数、等待复用数、退避状态、隔离账号与调度权重。"""
    with _states_lock:
        states = list(_states.items())
    now = _clock()
    return {
        "refreshes": dict(_counts),
        "reused": {acct: st.reused for acct, st in states},
        "backoff": {acct: st.next_try for acct, st in states
                    if st.next_try > now},
        "isolated": sorted(acct for acct, st in states if st.isolated),
        "weights": {acct: _weight(st) for acct, st in states},
    }


def audit_log():
    """全量审计记录：成功、失败、复用、退避、隔离、恢复。"""
    with _audit_lock:
        return [dict(record) for record in _audit_records]


def configure(*, refresh_ahead=None, backoff_base=None, backoff_max=None,
              isolate_threshold=None, token_ttl=None, issuer=None, clock=None,
              audit_path=None):
    """调整提前量、退避参数、隔离阈值、签发器、时钟和审计文件。"""
    global _REFRESH_AHEAD, _BACKOFF_BASE, _BACKOFF_MAX, _ISOLATE_THRESHOLD
    global _TOKEN_TTL, _issuer, _clock, _audit_path
    if refresh_ahead is not None:
        _REFRESH_AHEAD = float(refresh_ahead)
    if backoff_base is not None:
        _BACKOFF_BASE = float(backoff_base)
    if backoff_max is not None:
        _BACKOFF_MAX = float(backoff_max)
    if isolate_threshold is not None:
        _ISOLATE_THRESHOLD = int(isolate_threshold)
    if token_ttl is not None:
        _TOKEN_TTL = float(token_ttl)
    if issuer is not None:
        _issuer = issuer
    if clock is not None:
        _clock = clock
    if audit_path is not None:
        _audit_path = audit_path


def reset():
    """清空全部状态并恢复默认配置。"""
    global _seq, _issuer, _clock, _audit_path
    global _REFRESH_AHEAD, _BACKOFF_BASE, _BACKOFF_MAX
    global _ISOLATE_THRESHOLD, _TOKEN_TTL
    _counts.clear()
    _seq = 0
    with _states_lock:
        _states.clear()
    with _audit_lock:
        _audit_records.clear()
    _REFRESH_AHEAD = _DEFAULT_REFRESH_AHEAD
    _BACKOFF_BASE = _DEFAULT_BACKOFF_BASE
    _BACKOFF_MAX = _DEFAULT_BACKOFF_MAX
    _ISOLATE_THRESHOLD = _DEFAULT_ISOLATE_THRESHOLD
    _TOKEN_TTL = _DEFAULT_TOKEN_TTL
    _issuer = _default_issuer
    _clock = time.monotonic
    _audit_path = None
