"""账号登录态刷新：按账号单飞、提前刷新、指数退避、失败隔离与审计。

只依赖 Python 标准库。对外接口保持 ``get_token(account)`` 与
``refresh(account, force=False)`` 不变。
"""

import json
import threading
import time

# 口径（可通过 configure 覆盖）：提前量、退避基数/倍率/上限、隔离阈值。
DEFAULT_LEAD_TIME = 30.0
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_BACKOFF_CAP = 60.0
DEFAULT_ISOLATE_THRESHOLD = 3

NORMAL_WEIGHT = 1.0
ISOLATED_WEIGHT = 0.1

_counts = {}
_wait_counts = {}
_seq = 0

_registry = {}
_registry_lock = threading.Lock()

_audit_lock = threading.Lock()
_audit_records = []
_audit_file = None

_config = {
    "lead_time": DEFAULT_LEAD_TIME,
    "backoff_base": DEFAULT_BACKOFF_BASE,
    "backoff_factor": DEFAULT_BACKOFF_FACTOR,
    "backoff_cap": DEFAULT_BACKOFF_CAP,
    "isolate_threshold": DEFAULT_ISOLATE_THRESHOLD,
}

# 可被测试或 configure 替换的时钟与刷新后端。
_now = time.monotonic


class _AccountState:
    """单个账号的全部刷新状态，自带条件变量实现单飞通知。"""

    def __init__(self, account):
        self.account = account
        self.condition = threading.Condition()
        self.in_flight = False
        self.last_result = None
        self.token = None
        self.expires_at = None
        self.failures = 0
        self.next_try = None
        self.isolated = False
        self.weight = NORMAL_WEIGHT


def _default_backend(account):
    """默认后端：不带过期时间，因此每次取令牌都需要刷新（兼容旧行为）。"""
    global _seq
    _seq += 1
    return {"account": account, "token": "v" + str(_seq), "refreshed": True}


_refresh_backend = _default_backend


def configure(backend=None, lead_time=None, backoff_base=None,
              backoff_factor=None, backoff_cap=None,
              isolate_threshold=None, audit_file=None):
    """配置刷新后端、提前量、退避口径与审计文件，返回当前配置快照。"""
    global _refresh_backend, _audit_file
    if backend is not None:
        _refresh_backend = backend
    if audit_file is not None:
        _audit_file = audit_file
    overrides = {
        "lead_time": lead_time,
        "backoff_base": backoff_base,
        "backoff_factor": backoff_factor,
        "backoff_cap": backoff_cap,
        "isolate_threshold": isolate_threshold,
    }
    for key, value in overrides.items():
        if value is not None:
            _config[key] = value
    snapshot = dict(_config)
    snapshot["audit_file"] = _audit_file
    return snapshot


def _get_state(account):
    with _registry_lock:
        state = _registry.get(account)
        if state is None:
            state = _AccountState(account)
            _registry[account] = state
        return state


def _audit(event, account, **fields):
    record = {"time": _now(), "event": event, "account": account}
    record.update(fields)
    with _audit_lock:
        _audit_records.append(record)
        if _audit_file is not None:
            with open(_audit_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _failure_result(state, error, now, backoff_skip=False):
    return {
        "account": state.account,
        "token": state.token,
        "refreshed": False,
        "ok": False,
        "error": str(error),
        "backoff": backoff_skip,
        "stale": state.token is not None,
        "failures": state.failures,
        "next_try": state.next_try,
        "isolated": state.isolated,
        "weight": state.weight,
        "now": now,
    }


def _do_refresh(state, force):
    """仅由单飞 leader 调用：处理退避短路、后端调用、成功/失败记账。"""
    account = state.account
    now = _now()

    if not force and state.next_try is not None and now < state.next_try:
        _audit("backoff_skip", account, next_try=state.next_try,
               failures=state.failures, isolated=state.isolated)
        return _failure_result(state, "in backoff window", now,
                               backoff_skip=True)

    if (not force and state.token is not None and state.expires_at is not None
            and now < state.expires_at - _config["lead_time"]):
        return dict(state.last_result)

    _audit("refresh_start", account, force=force)
    try:
        payload = _refresh_backend(account)
    except Exception as exc:  # 后端任何异常都走退避，避免风暴打挂账号
        with state.condition:
            state.failures += 1
            backoff = min(
                _config["backoff_cap"],
                _config["backoff_base"]
                * _config["backoff_factor"] ** (state.failures - 1),
            )
            state.next_try = now + backoff
            if state.failures >= _config["isolate_threshold"]:
                state.isolated = True
                state.weight = ISOLATED_WEIGHT
            result = _failure_result(state, exc, now)
        _audit("refresh_failure", account, error=str(exc),
               failures=state.failures, next_try=state.next_try,
               isolated=state.isolated, weight=state.weight)
        return result

    ttl = payload.get("expires_in")
    expires_at = now + ttl if ttl is not None else None
    result = {
        "account": account,
        "token": payload["token"],
        "refreshed": True,
        "ok": True,
        "expires_at": expires_at,
    }
    with state.condition:
        state.token = result["token"]
        state.expires_at = expires_at
        state.failures = 0
        state.next_try = None
        state.isolated = False
        state.weight = NORMAL_WEIGHT
        state.last_result = result
        _counts[account] = _counts.get(account, 0) + 1
    _audit("refresh_success", account, token=result["token"],
           expires_at=expires_at)
    return dict(result)


def refresh(account, force=False):
    """刷新账号令牌。

    同一账号并发调用时只有一个请求真正执行，其余线程等待并复用同一个
    结果；``force=True`` 绕过硬过期与退避判断，但仍参与单飞。
    """
    state = _get_state(account)
    with state.condition:
        if state.in_flight:
            _wait_counts[account] = _wait_counts.get(account, 0) + 1
            while state.in_flight:
                state.condition.wait()
            _audit("wait_reuse", account,
                   waited=True, token=state.token)
            return dict(state.last_result) if state.last_result else None
        state.in_flight = True

    result = None
    try:
        result = _do_refresh(state, force)
    finally:
        with state.condition:
            if result is None:  # 未预期异常也要保证单飞等待方拿到结果
                result = _failure_result(state, "refresh crashed", _now())
            state.last_result = result
            state.in_flight = False
            state.condition.notify_all()
    return result


def get_token(account):
    """取可用令牌：未到提前量直接复用缓存，否则触发（单飞）刷新。"""
    state = _get_state(account)
    with state.condition:
        in_flight = state.in_flight
        token = state.token
        expires_at = state.expires_at
        cached = dict(state.last_result) if state.last_result else None

    if in_flight:
        return refresh(account)
    if (token is not None and expires_at is not None
            and _now() < expires_at - _config["lead_time"]):
        return cached
    return refresh(account)


def stats():
    """兼容旧接口：每个账号实际打到后端的刷新次数。"""
    return dict(_counts)


def account_status(account):
    """返回单个账号的退避、隔离、权重与缓存令牌状态。"""
    state = _get_state(account)
    with state.condition:
        return {
            "account": account,
            "token": state.token,
            "expires_at": state.expires_at,
            "failures": state.failures,
            "next_try": state.next_try,
            "isolated": state.isolated,
            "weight": state.weight,
            "in_flight": state.in_flight,
        }


def next_try_at(account):
    """下次允许尝试刷新的时间（单调时钟），未退避时为 None。"""
    return _get_state(account).next_try


def is_isolated(account):
    return _get_state(account).isolated


def weight(account):
    """账号调度权重：正常 1.0，隔离后降到 ISOLATED_WEIGHT。"""
    return _get_state(account).weight


def audit_records():
    """返回审计记录快照。"""
    with _audit_lock:
        return [dict(record) for record in _audit_records]


def report():
    """汇总刷新次数、等待复用数、退避状态与隔离账号。"""
    with _registry_lock:
        accounts = list(_registry)
    backoff = {}
    isolated = []
    for account in accounts:
        status = account_status(account)
        if status["next_try"] is not None or status["isolated"]:
            backoff[account] = {
                "failures": status["failures"],
                "next_try": status["next_try"],
                "isolated": status["isolated"],
                "weight": status["weight"],
            }
        if status["isolated"]:
            isolated.append(account)
    return {
        "refresh_counts": dict(_counts),
        "total_refreshes": sum(_counts.values()),
        "wait_reuse": dict(_wait_counts),
        "total_wait_reuse": sum(_wait_counts.values()),
        "backoff": backoff,
        "isolated": isolated,
    }


def reset():
    """清空全部运行时状态（测试用），配置恢复默认。"""
    global _seq, _refresh_backend, _audit_file
    with _registry_lock:
        _registry.clear()
    _counts.clear()
    _wait_counts.clear()
    _audit_records.clear()
    _seq = 0
    _audit_file = None
    _refresh_backend = _default_backend
    _config.update({
        "lead_time": DEFAULT_LEAD_TIME,
        "backoff_base": DEFAULT_BACKOFF_BASE,
        "backoff_factor": DEFAULT_BACKOFF_FACTOR,
        "backoff_cap": DEFAULT_BACKOFF_CAP,
        "isolate_threshold": DEFAULT_ISOLATE_THRESHOLD,
    })
