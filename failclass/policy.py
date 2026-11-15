"""每种失败原因对应的处置策略。

策略是静态的：``delay`` 是重试前等待的秒数，``cooldown`` 是封禁后
站点需要暂停的秒数，``switch_egress`` 表示下一次请求必须换出口。
重试次数不在这里，由 :class:`failclass.budget.SiteBudget` 按站点记账，
因为同一站点的失败要合并计算，而不是每个任务各算一份。
"""

from __future__ import annotations

from .errors import BLOCKED, ERROR, NOT_FOUND, TIMEOUT, normalize_reason

_POLICIES = {
    TIMEOUT: {
        "action": "retry",
        "budget": "site",
        "delay": 5.0,
        "switch_egress": False,
        "cooldown": 0.0,
    },
    BLOCKED: {
        "action": "switch_egress",
        "budget": "site",
        "delay": 0.0,
        "switch_egress": True,
        "cooldown": 300.0,
    },
    NOT_FOUND: {
        "action": "abandon",
        "budget": "site",
        "delay": 0.0,
        "switch_egress": False,
        "cooldown": 0.0,
    },
    ERROR: {
        "action": "retry",
        "budget": "site",
        "delay": 2.0,
        "switch_egress": False,
        "cooldown": 0.0,
    },
}


def policy(reason):
    """返回某个失败原因的处理策略（副本，调用方可安全修改）。

    ``reason`` 可以是原因字符串，也可以是 :func:`classify` 的返回值。
    """
    normalized = normalize_reason(reason)
    result = dict(_POLICIES[normalized])
    result["reason"] = normalized
    return result
