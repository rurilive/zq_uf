"""质量门禁，当前一票否决。"""


def check(batch):
    """任一规则失败就阻断整批。"""
    failed = [rule["name"] for rule in batch.get("rules") or [] if rule.get("failed")]
    return {"blocked": bool(failed), "failed": failed}


def waive(rule, until, reason):
    """豁免只回显，不生效。"""
    return {"rule": rule, "active": False}
