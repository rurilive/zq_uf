"""弱项判定规则：协议版本、套件强度、证书与重定向。"""

SEVERITY_ORDER = {"critical": 3, "high": 2, "medium": 1, "low": 0}
CATEGORY_ORDER = {"protocol": 0, "cipher": 1, "certificate": 2}

KNOWN_PROTOCOLS = ("SSLv2", "SSLv3", "TLSv1.0", "TLSv1.1", "TLSv1.2", "TLSv1.3")

PROTOCOL_EXPECTED = "TLSv1.2 及以上（推荐 TLSv1.3）"

# 协议 -> (严重程度, 说明)
PROTOCOL_RULES = {
    "SSLv2": ("critical", "已废弃的 SSLv2，存在严重设计缺陷"),
    "SSLv3": ("critical", "已废弃的 SSLv3，存在 POODLE 漏洞"),
    "TLSv1.0": ("high", "已淘汰的 TLSv1.0，不再满足合规要求"),
    "TLSv1.1": ("medium", "已淘汰的 TLSv1.1，主流浏览器已停止支持"),
}
CIPHER_EXPECTED = {
    "critical": "禁用该套件，改用 ECDHE + AEAD（如 ECDHE-RSA-AES256-GCM-SHA384）",
    "high": "禁用 3DES/DES，改用 ECDHE + AEAD 套件",
    "medium": "改用带前向保密的 ECDHE 套件",
    "low": "改用带前向保密的 ECDHE + AEAD 套件",
}


def severity_rank(severity):
    """严重程度排序权重，未知级别按最低处理。"""
    return SEVERITY_ORDER.get(severity, -1)


def item_sort_key(item):
    """单入口内排序：严重程度优先，其次分类。"""
    return (-severity_rank(item["severity"]), CATEGORY_ORDER.get(item["category"], 9),
            str(item.get("actual", "")))


def make_item(host, endpoint, category, severity, actual, expected, detail):
    return {
        "host": host,
        "endpoint": endpoint,
        "category": category,
        "severity": severity,
        "actual": actual,
        "expected": expected,
        "detail": detail,
    }


def protocol_items(host, endpoint, protocols):
    """逐个检查开放的协议版本，只保留不达标的。"""
    items = []
    for protocol in protocols:
        rule = PROTOCOL_RULES.get(protocol)
        if rule is None:
            continue
        severity, detail = rule
        items.append(make_item(host, endpoint, "protocol", severity,
                               protocol, PROTOCOL_EXPECTED, detail))
    return items


def cipher_severity(suite):
    """按套件名判断强度，返回 (严重程度或 None, 说明)。

    判定顺序：已破解/可降级套件 > 64 位块加密 > 前向保密 > 静态 RSA 的 AEAD > CBC。
    TLS 1.3 的 TLS_AES_* / TLS_CHACHA20_* 自带前向保密，按强套件处理。
    """
    name = str(suite).upper()
    if any(token in name for token in ("NULL", "EXPORT", "ANON", "ADH", "RC4", "MD5")):
        return "critical", "已破解或可被降级利用的套件"
    if "3DES" in name or "DES-CBC3" in name or name.startswith("DES-"):
        return "high", "64 位块加密（3DES/DES），存在 Sweet32 风险"
    forward_secret = (
        "ECDHE" in name or "DHE-" in name or "_DHE_" in name
        or name.startswith(("TLS_AES_", "TLS_CHACHA20_"))
    )
    if forward_secret:
        return None, "支持前向保密的套件"
    if any(token in name for token in ("GCM", "CHACHA20", "CCM")):
        return "low", "AEAD 套件但使用静态 RSA 密钥交换，缺少前向保密"
    return "medium", "CBC 模式且无前向保密"


def cipher_items(host, endpoint, ciphers):
    """逐个检查协商套件，弱套件按强度分级。"""
    items = []
    for suite in ciphers:
        severity, detail = cipher_severity(suite)
        if severity is None:
            continue
        items.append(make_item(host, endpoint, "cipher", severity,
                               suite, CIPHER_EXPECTED[severity], detail))
    return items


def certificate_items(host, endpoint, days_left, chain_ok):
    """证书有效期与签发链问题，归入证书类弱项。"""
    items = []
    if days_left <= 0:
        items.append(make_item(host, endpoint, "certificate", "critical",
                               f"剩余 {days_left} 天（已过期）", "有效期大于 0 天",
                               "证书已过期，客户端会直接拒绝连接"))
    elif days_left <= 7:
        items.append(make_item(host, endpoint, "certificate", "high",
                               f"剩余 {days_left} 天", "剩余 30 天以上",
                               "一周内到期，需立即更换"))
    elif days_left <= 30:
        items.append(make_item(host, endpoint, "certificate", "medium",
                               f"剩余 {days_left} 天", "剩余 30 天以上",
                               "剩余有效期不足 30 天，应安排更换"))
    if not chain_ok:
        items.append(make_item(host, endpoint, "certificate", "high",
                               "签发链不完整", "完整签发链（含中间证书）",
                               "缺少中间证书，部分客户端校验失败"))
    return items


def cert_status(days_left, within):
    """证书状态：有效 / 到期提醒窗口内 / 已过期。"""
    if days_left <= 0:
        return "expired"
    if days_left <= within:
        return "expiring"
    return "valid"


def weakest_protocol(protocols):
    """最弱的协议版本，用于入口概览。"""
    if not protocols:
        return None
    return min(protocols, key=lambda p: KNOWN_PROTOCOLS.index(p)
               if p in KNOWN_PROTOCOLS else len(KNOWN_PROTOCOLS))


def strongest_protocol(protocols):
    if not protocols:
        return None
    return max(protocols, key=lambda p: KNOWN_PROTOCOLS.index(p)
               if p in KNOWN_PROTOCOLS else -1)


def worst_severity(items):
    if not items:
        return None
    return max(items, key=lambda item: severity_rank(item["severity"]))["severity"]


def verdict(items, redirect_ok):
    """入口结论：fail / warn / ok。重定向缺失单独降为 warn。"""
    worst = worst_severity(items)
    if worst in ("critical", "high"):
        return "fail"
    if worst in ("medium", "low") or not redirect_ok:
        return "warn"
    return "ok"


def worst_verdict(verdicts):
    rank = {"ok": 0, "warn": 1, "fail": 2}
    return max(verdicts, key=lambda v: rank.get(v, 0)) if verdicts else "ok"
