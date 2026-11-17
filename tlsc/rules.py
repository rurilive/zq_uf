"""判弱规则：协议版本、套件强度、证书问题、重定向。

每条规则返回一个 :class:`WeakItem`（含实际取值与期望取值），不命中返回 None。
期望值给出可落地的整改目标，而不是笼统的"升级"。
"""

from __future__ import annotations

from typing import Optional

from .models import (
    CATEGORY_CERT,
    CATEGORY_CIPHER,
    CATEGORY_PROTOCOL,
    CATEGORY_REDIRECT,
    Entry,
    SEVERITY_RANK,
    WeakItem,
)

# 到期提醒默认阈值（天）：<= 30 天提前标出
DEFAULT_WARN_DAYS = 30

# 协议版本 -> 严重程度
_PROTOCOL_SEVERITY = {
    "SSLv2": "critical",
    "SSLv3": "critical",
    "TLSv1.0": "high",
    "TLSv1.1": "high",
}
_EXPECTED_PROTOCOL = "TLSv1.2 或 TLSv1.3"

# 套件名片段（大写）-> 严重程度；命中即弱，先命中先判
_CIPHER_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("NULL",), "critical"),
    (("EXPORT",), "critical"),
    (("ANON",), "critical"),
    (("RC4",), "critical"),
    (("DES-CBC3", "3DES"), "high"),
    (("CBC3",), "high"),
    (("3DES",), "high"),
    # CBC + SHA1 套件（如 AES128-SHA、AES256-SHA）认证强度不足
    (("CBC-SHA",), "medium"),
)
# 老命名里 CBC-SHA 不带 CBC 字样（AES128-SHA / AES256-SHA），单独补一条
_LEGACY_SHA1_CIPHER = {"AES128-SHA", "AES256-SHA"}
_EXPECTED_CIPHER = "启用 AEAD 套件（GCM/CHACHA20，推荐 TLS1.3 套件），禁用 RC4/3DES/EXPORT/NULL 及 CBC-SHA1"


def check_protocol(entry: Entry) -> Optional[WeakItem]:
    if entry.protocol is None:
        return None
    severity = _PROTOCOL_SEVERITY.get(entry.protocol)
    if severity is None:
        return None
    return WeakItem(
        category=CATEGORY_PROTOCOL,
        severity=severity,
        item="protocol_version",
        actual=entry.protocol,
        expected=_EXPECTED_PROTOCOL,
    )


def check_cipher(entry: Entry) -> Optional[WeakItem]:
    if entry.cipher is None:
        return None
    name = entry.cipher.upper()
    for fragments, severity in _CIPHER_RULES:
        if any(fragment in name for fragment in fragments):
            return WeakItem(
                category=CATEGORY_CIPHER,
                severity=severity,
                item="cipher_strength",
                actual=entry.cipher,
                expected=_EXPECTED_CIPHER,
            )
    if entry.cipher in _LEGACY_SHA1_CIPHER:
        return WeakItem(
            category=CATEGORY_CIPHER,
            severity="medium",
            item="cipher_strength",
            actual=entry.cipher,
            expected=_EXPECTED_CIPHER,
        )
    return None


def check_cert(entry: Entry, warn_days: int = DEFAULT_WARN_DAYS) -> list[WeakItem]:
    """证书问题：已过期、即将到期、自签名、签发链不完整。"""
    findings: list[WeakItem] = []

    if entry.days_left is not None:
        if entry.days_left <= 0:
            findings.append(
                WeakItem(
                    category=CATEGORY_CERT,
                    severity="critical",
                    item="cert_expired",
                    actual=f"已过期 {-entry.days_left} 天",
                    expected="更换有效证书",
                )
            )
        elif entry.days_left <= warn_days:
            findings.append(
                WeakItem(
                    category=CATEGORY_CERT,
                    severity="low",
                    item="cert_expiring",
                    actual=f"剩余 {entry.days_left} 天",
                    expected=f"在 {warn_days} 天阈值内完成更换",
                )
            )

    chain = entry.issuer_chain
    if chain:
        if len(chain) == 1:
            findings.append(
                WeakItem(
                    category=CATEGORY_CERT,
                    severity="high",
                    item="cert_self_signed",
                    actual=f"自签名证书：{chain[0]}",
                    expected="由受信任 CA 签发并提供完整链",
                )
            )
        elif len(chain) == 2:
            findings.append(
                WeakItem(
                    category=CATEGORY_CERT,
                    severity="medium",
                    item="cert_chain_incomplete",
                    actual=f"链上仅 {len(chain)} 级（{ ' -> '.join(chain) }），缺中间 CA",
                    expected="完整链：根 CA -> 中间 CA -> 本站证书",
                )
            )
    return findings


def check_redirect(entry: Entry) -> Optional[WeakItem]:
    """HTTP 入口必须 301/302 跳到 HTTPS；HTTPS 入口不做要求。"""
    if entry.scheme != "http":
        return None
    target = entry.redirect_target or ""
    if entry.redirect is None or not target.lower().startswith("https://"):
        return WeakItem(
            category=CATEGORY_REDIRECT,
            severity="medium",
            item="http_redirect",
            actual=(
                "不跳转"
                if entry.redirect is None
                else f"{entry.redirect} 跳转到 {target or '空目标'}"
            ),
            expected="301/302 跳转到对应的 https:// 入口",
        )
    return None


def evaluate(entry: Entry, warn_days: int = DEFAULT_WARN_DAYS) -> list[WeakItem]:
    """对单个入口跑全部规则，弱项按严重程度排序。"""
    findings: list[WeakItem] = []
    for outcome in (
        check_protocol(entry),
        check_cipher(entry),
        check_redirect(entry),
    ):
        if outcome is not None:
            findings.append(outcome)
    findings.extend(check_cert(entry, warn_days=warn_days))
    findings.sort(key=lambda item: SEVERITY_RANK[item.severity])
    return findings
