"""账号登录态刷新：按账号单飞、提前刷新、指数退避、失败隔离。"""

import json
import threading
import time

EARLY_REFRESH_S = 30.0   # 提前量：距过期不足该值即刷新
BACKOFF_BASE_S = 1.0     # 退避基数，随连续失败翻倍
BACKOFF_MAX_S = 60.0     # 单次退避上限
ISOLATE_AFTER = 3        # 连续失败达到该次数即隔离
ISOLATION_S = 300.0      # 隔离冷却时长

_counts = {}    # account -> 成功刷新次数（stats 旧口径）
_waits = {}     # account -> 等待复用次数
_accounts = {}  # account -> _AccountState
_audit = []
_audit_path = None
_issuer = None
_seq = 0

_guard = threading.Lock()  # 只保护字典本身，不跨账号持有


class _AccountState:
    def __init__(self):
        self.lock = threading.Lock()
        self.token = None
        self.expires_at = 0.0
        self.failures = 0
        self.next_try = 0.0
        self.isolated = False
        self.isolated_until = 0.0
        self.weight = 1.0


def _now():
    return time.monotonic()


def _state_for(account):
    with _guard:
        state = _accounts.get(account)
        if state is None:
            state = _accounts[account] = _AccountState()
        return state


def set_issuer(fn):
    """注入签发器：fn(account) -> {"token": str, "expires_in": 秒}，失败抛异常。"""
    global _issuer
    _issuer = fn


def set_audit_file(path):
    """设置审计落盘路径（JSON Lines 追加），None 关闭。"""
    global _audit_path
    _audit_path = path


def _record_audit(event, account, **fields):
    entry = {"ts": time.time(), "event": event, "account": account}
    entry.update(fields)
    with _guard:
        _audit.append(entry)
        path = _audit_path
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def audit_log():
    with _guard:
        return list(_audit)


def _default_issue(account):
    """默认签发器：无过期信息，视为立即过期（保持每次请求都刷新的旧口径）。"""
    global _seq
    with _guard:
        _seq += 1
        seq = _seq
    return {"token": "v" + str(seq), "expires_in": 0}


def _valid(state, now):
    return state.token is not None and now < state.expires_at - EARLY_REFRESH_S


def refresh(account, force=False):
    """刷新账号令牌；force=True 绕过缓存、退避与隔离直接重试。"""
    state = _state_for(account)
    if state.lock.locked():
        with _guard:
            _waits[account] = _waits.get(account, 0) + 1
    with state.lock:
        now = _now()
        if not force:
            if state.isolated and now < state.isolated_until:
                return {"account": account, "token": state.token,
                        "refreshed": False, "isolated": True,
                        "next_try": state.next_try}
            if now < state.next_try:
                return {"account": account, "token": state.token,
                        "refreshed": False, "backoff": True,
                        "next_try": state.next_try}
            if _valid(state, now):
                return {"account": account, "token": state.token,
                        "refreshed": False}
        return _do_refresh(account, state)


def _do_refresh(account, state):
    now = _now()
    issue = _issuer or _default_issue
    try:
        issued = issue(account)
    except Exception as exc:
        state.failures += 1
        delay = min(BACKOFF_BASE_S * (2 ** (state.failures - 1)), BACKOFF_MAX_S)
        state.next_try = now + delay
        state.weight = 1.0 / (2 ** state.failures)
        if state.failures >= ISOLATE_AFTER:
            state.isolated = True
            state.isolated_until = now + ISOLATION_S
            state.next_try = max(state.next_try, state.isolated_until)
            state.weight = 0.0
            _record_audit("isolated", account, failures=state.failures,
                          isolated_until=state.isolated_until)
        _record_audit("refresh_failed", account, error=str(exc),
                      failures=state.failures, next_try=state.next_try)
        return {"account": account, "token": state.token, "refreshed": False,
                "error": str(exc), "next_try": state.next_try,
                "isolated": state.isolated, "weight": state.weight}
    token = issued["token"]
    expires_in = float(issued.get("expires_in", 0))
    state.token = token
    state.expires_at = now + expires_in
    state.failures = 0
    state.next_try = 0.0
    state.isolated = False
    state.isolated_until = 0.0
    state.weight = 1.0
    with _guard:
        _counts[account] = _counts.get(account, 0) + 1
    _record_audit("refresh_ok", account, token=token, expires_in=expires_in)
    return {"account": account, "token": token, "refreshed": True,
            "expires_at": state.expires_at}


def get_token(account):
    """取令牌：有效期内直接复用，临期或失效走单飞刷新。"""
    state = _state_for(account)
    if _valid(state, _now()):
        return {"account": account, "token": state.token, "refreshed": False}
    return refresh(account)


def stats():
    with _guard:
        return dict(_counts)


def report():
    """刷新次数、等待复用数、退避状态（剩余秒）、隔离账号与调度权重。"""
    now = _now()
    with _guard:
        states = dict(_accounts)
        waits = dict(_waits)
        counts = dict(_counts)
    return {
        "refreshes": counts,
        "waits": waits,
        "backoff": {a: round(s.next_try - now, 3) for a, s in states.items()
                    if s.next_try > now},
        "isolated": sorted(a for a, s in states.items()
                           if s.isolated and s.isolated_until > now),
        "weights": {a: s.weight for a, s in states.items()},
    }
