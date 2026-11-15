"""抓取失败分类与处置策略。

失败归一为四类：timeout / banned / not_found / error，
证据附状态码和响应头；不同原因对应不同下一步动作。
"""

REASON_TIMEOUT = "timeout"
REASON_BANNED = "banned"
REASON_NOT_FOUND = "not_found"
REASON_ERROR = "error"

REASONS = (REASON_TIMEOUT, REASON_BANNED, REASON_NOT_FOUND, REASON_ERROR)

_NOT_FOUND_STATUSES = {404, 410}
_BAN_STATUSES = {401, 403, 429, 503}


def _response_parts(response):
    """从响应对象或 dict 里取状态码和响应头。"""
    if response is None:
        return None, {}
    if isinstance(response, dict):
        return response.get("status_code"), dict(response.get("headers") or {})
    return (
        getattr(response, "status_code", None),
        dict(getattr(response, "headers", None) or {}),
    )


def classify(error, response=None):
    """归一失败原因，返回 {"reason", "evidence"}，证据含状态码和响应头。"""
    status, headers = _response_parts(response)
    evidence = {"error": str(error), "status_code": status, "headers": headers}
    text = str(error).lower()
    if status in _NOT_FOUND_STATUSES:
        reason = REASON_NOT_FOUND
    elif status in _BAN_STATUSES:
        reason = REASON_BANNED
    elif "timeout" in text or "timed out" in text:
        reason = REASON_TIMEOUT
    else:
        reason = REASON_ERROR
    return {"reason": reason, "evidence": evidence}


# 每种原因的处置：下一步动作、等待秒数、是否更换出口。
# 预算一律按站点共享（budget="site"），由 SiteLedger 执行。
_POLICIES = {
    # 超时：等一会儿再试，不换出口。
    REASON_TIMEOUT: {"action": "retry", "delay": 30, "switch_exit": False},
    # 封禁：换出口并冷却一段时间，冷却结束自动恢复。
    REASON_BANNED: {"action": "cooldown", "delay": 600, "switch_exit": True},
    # 页面不存在：直接放弃，进人工复核清单。
    REASON_NOT_FOUND: {"action": "give_up", "delay": 0, "switch_exit": False},
    # 其他错误：短等待后重试。
    REASON_ERROR: {"action": "retry", "delay": 5, "switch_exit": False},
}


def policy(reason):
    """返回该原因的处置策略；预算按站点共享。"""
    if reason not in _POLICIES:
        raise ValueError(f"unknown reason: {reason!r}")
    return dict(_POLICIES[reason], budget="site")
