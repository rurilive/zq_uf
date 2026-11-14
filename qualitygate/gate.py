"""分级质量门禁：阻断 / 告警 / 提示三级，按失败行数与影响范围决策。

规则通过 ``level`` 声明级别（``block`` / ``warning`` / ``info``），缺省为
``block``，保持历史一票否决口径。豁免按规则名登记，带到期时间与原因，
到期自动失效并写入审计日志。
"""

import time

LEVEL_BLOCK = "block"
LEVEL_WARNING = "warning"
LEVEL_INFO = "info"
LEVELS = (LEVEL_BLOCK, LEVEL_WARNING, LEVEL_INFO)

DEFAULT_WARNING_ROWS = 1000
DEFAULT_WARNING_RATIO = 0.01

_WAIVERS = {}
_AUDIT = []


def _now():
    return time.time()


def _record_audit(event, rule, **detail):
    entry = {"event": event, "rule": rule, "at": _now()}
    entry.update(detail)
    _AUDIT.append(entry)


def _sweep(now):
    """把已到期的生效豁免一次性转为过期并记审计，重复调用无副作用。"""
    for record in _WAIVERS.values():
        if record["active"] and record["until"] <= now:
            record["active"] = False
            _record_audit(
                "expired",
                record["rule"],
                until=record["until"],
                reason=record["reason"],
            )


def waive(rule, until, reason):
    """登记一条带到期时间（epoch 秒）和原因的豁免。

    ``until`` 已过期时豁免仍登记留痕，但立即失效并记过期审计。
    """
    now = _now()
    if not isinstance(rule, str) or not rule.strip():
        raise ValueError("rule name is required")
    if isinstance(until, bool) or not isinstance(until, (int, float)):
        raise TypeError("until must be an epoch timestamp in seconds")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("waiver reason is required")

    active = until > now
    _WAIVERS[rule] = {"rule": rule, "until": until, "reason": reason, "active": active}
    _record_audit("waived", rule, until=until, reason=reason)
    if not active:
        _record_audit("expired", rule, until=until, reason=reason)
    return {"rule": rule, "until": until, "reason": reason, "active": active}


def _resolve_thresholds(batch):
    configured = batch.get("thresholds") or {}
    return {
        "warning_rows": configured.get("warning_rows", DEFAULT_WARNING_ROWS),
        "warning_ratio": configured.get("warning_ratio", DEFAULT_WARNING_RATIO),
    }


def _warning_blocks(rule, thresholds):
    failed_rows = rule.get("failed_rows", 1)
    if failed_rows >= thresholds["warning_rows"]:
        return True
    total_rows = rule.get("total_rows")
    if total_rows:
        return failed_rows / total_rows >= thresholds["warning_ratio"]
    return False


def check(batch):
    """评估一批规则，返回分级失败数、阻断结论和生效豁免。

    同批数据重复调用结果一致：``check`` 不把时间戳写进结果，过期清扫
    也是幂等的。
    """
    now = _now()
    _sweep(now)
    thresholds = _resolve_thresholds(batch)

    counts = {LEVEL_BLOCK: 0, LEVEL_WARNING: 0, LEVEL_INFO: 0}
    failed = []
    waived = []
    active_waivers = []
    blocked = False

    for rule in batch.get("rules") or []:
        if not rule.get("failed"):
            continue
        name = rule.get("name")
        level = rule.get("level")
        if level not in LEVELS:
            level = LEVEL_BLOCK

        record = _WAIVERS.get(name)
        if record is not None and record["active"] and record["until"] > now:
            waived.append(name)
            active_waivers.append(
                {"rule": name, "until": record["until"], "reason": record["reason"]}
            )
            continue

        counts[level] += 1
        failed.append(name)
        if level == LEVEL_BLOCK:
            blocked = True
        elif level == LEVEL_WARNING and _warning_blocks(rule, thresholds):
            blocked = True

    return {
        "blocked": blocked,
        "failed": failed,
        "counts": counts,
        "waived": waived,
        "active_waivers": active_waivers,
    }


def audit():
    """返回豁免生命周期审计记录的副本（登记 / 过期）。"""
    return [dict(entry) for entry in _AUDIT]


def _reset():
    _WAIVERS.clear()
    _AUDIT.clear()
