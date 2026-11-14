"""告警：按环境区分级别门槛与通知通道。

口径：
- 每个环境有自己的 min_level 与通道；未配置的环境走 default（只收 high）。
- 告警文本按环境拼前缀，敏感列只出现列名与哈希，不出现明文。
- default_alerts() 返回内存里收集到的告警，便于测试与自检，不写外部通道。
"""

from .drift import LEVEL_RANK

DEFAULT_POLICY = {
    "default": {"min_level": "high", "sink": "memory"},
    "prod": {"min_level": "medium", "sink": "memory"},
    "staging": {"min_level": "medium", "sink": "memory"},
    "dev": {"min_level": "low", "sink": "memory"},
}


def _policy_for(env, policies):
    policies = policies or DEFAULT_POLICY
    return policies.get(str(env)) or policies.get("default") or DEFAULT_POLICY["default"]


def should_alert(report, policies=None, env=None):
    """判断报告是否触发该环境的告警。"""
    env_name = str(env or report.get("target_env") or report.get("env") or "default")
    policy = _policy_for(env_name, policies)
    if not report.get("has_drift"):
        return False
    min_level = policy.get("min_level", "high")
    threshold = LEVEL_RANK.get(min_level, LEVEL_RANK["high"])
    return any(
        LEVEL_RANK.get(change.get("level"), 0) >= threshold
        for change in report.get("changes", [])
    )


def build_alert(report, policies=None, env=None):
    """生成告警对象；不触发时返回 None。"""
    env_name = str(env or report.get("target_env") or report.get("env") or "default")
    if not should_alert(report, policies, env=env_name):
        return None
    policy = _policy_for(env_name, policies)
    table = report.get("table")

    lines = ["[schemadrift][%s] 表 %s 检测到结构漂移，最高级别 %s" % (
        env_name, table, report.get("level"))]
    for change in report.get("changes", []):
        if LEVEL_RANK.get(change.get("level"), 0) < LEVEL_RANK.get(policy.get("min_level", "high"), 0):
            continue
        detail = "%s %s:%s" % (change.get("level"), change.get("kind"), change.get("column"))
        if change.get("attribute"):
            detail += " (%s)" % change["attribute"]
        if change.get("redacted"):
            detail += " [敏感列，默认值仅比较哈希]"
        lines.append("- " + detail)
        lines.append("  建议: " + str(change.get("suggestion", "")))

    return {
        "env": env_name,
        "table": table,
        "level": report.get("level"),
        "min_level": policy.get("min_level", "high"),
        "sink": policy.get("sink", "memory"),
        "message": "\n".join(lines),
        "changes": report.get("changes", []),
    }


def dispatch(alert, sink=None):
    """把告警投递到通道；memory 通道原样返回，便于测试。"""
    if alert is None:
        return None
    target = sink or alert.get("sink") or "memory"
    if target == "memory":
        return alert
    raise ValueError("不支持的告警通道：%s（默认仅支持 memory）" % target)
