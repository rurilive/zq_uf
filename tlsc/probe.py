"""证书与协议核查：覆盖域名下的全部入口，按入口单独给结论。

- check(host)        核查一个域名的全部入口（证书有效期、签发链、协议、套件、重定向）
- report()           汇总全部域名：入口清单、证书状态、协议与套件、弱项、到期提醒
- recheck(host=...)  整改后增量重查：只重新评估配置指纹发生变化的入口
- diff_entries(host) 同一域名多个入口的配置差异对比
- expiring(within)   到期提醒（提前 within 天标出）

数据为登记式快照（模拟探测结果）；规则集中在 :mod:`tlsc.rules`。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .models import SEVERITY_ORDER, SEVERITY_RANK, Entry, WeakItem
from .rules import DEFAULT_WARN_DAYS, evaluate

# ---------------------------------------------------------------------------
# 登记数据：同一域名可挂多个入口（主/备/灰度/CDN/HTTP 跳转入口）
# ---------------------------------------------------------------------------

_HOSTS: dict[str, list[Entry]] = {
    "shop.example.com": [
        Entry(
            entry_id="primary",
            address="10.1.0.11",
            port=443,
            days_left=12,
            issuer_chain=("DigiCert Global Root CA", "shop.example.com"),
            protocol="TLSv1.0",
            cipher="RC4-SHA",
        ),
        Entry(
            entry_id="backup",
            address="10.1.0.12",
            port=443,
            days_left=200,
            issuer_chain=(
                "DigiCert Global Root CA",
                "DigiCert TLS RSA CA",
                "shop.example.com",
            ),
            protocol="TLSv1.2",
            cipher="AES128-SHA",
        ),
        Entry(
            entry_id="http-redirect",
            address="10.1.0.11",
            port=80,
            scheme="http",
            redirect="301",
            redirect_target="https://shop.example.com/",
        ),
    ],
    "api.example.com": [
        Entry(
            entry_id="primary",
            address="10.1.0.21",
            port=443,
            days_left=210,
            issuer_chain=(
                "GlobalSign Root CA",
                "GlobalSign R3 TLS CA",
                "api.example.com",
            ),
            protocol="TLSv1.2",
            cipher="ECDHE-RSA-AES128-GCM-SHA256",
            redirect="302",
            redirect_target="https://api.example.com/v2",
        ),
        Entry(
            entry_id="canary",
            address="10.1.0.22",
            port=443,
            days_left=210,
            issuer_chain=(
                "GlobalSign Root CA",
                "GlobalSign R3 TLS CA",
                "api.example.com",
            ),
            protocol="TLSv1.1",
            cipher="ECDHE-RSA-AES128-GCM-SHA256",
        ),
        Entry(
            entry_id="http-redirect",
            address="10.1.0.21",
            port=80,
            scheme="http",
        ),  # 不跳转 -> 弱项
    ],
    "cdn.example.com": [
        Entry(
            entry_id="edge-a",
            address="10.1.0.31",
            port=443,
            days_left=45,
            issuer_chain=("GlobalSign Root CA", "GlobalSign R3 TLS CA", "cdn.example.com"),
            protocol="TLSv1.2",
            cipher="ECDHE-RSA-AES256-GCM-SHA384",
        ),
        Entry(
            entry_id="edge-b",
            address="10.1.0.32",
            port=443,
            days_left=45,
            issuer_chain=("GlobalSign Root CA", "GlobalSign R3 TLS CA", "cdn.example.com"),
            protocol="TLSv1.2",
            cipher="ECDHE-RSA-AES256-GCM-SHA384",
        ),
        Entry(
            entry_id="edge-legacy",
            address="10.1.0.33",
            port=443,
            days_left=-3,  # 已过期
            issuer_chain=("cdn.example.com",),  # 自签名
            protocol="SSLv3",
            cipher="DES-CBC3-SHA",
        ),
    ],
}


def _entries(host: str) -> list[Entry]:
    if host not in _HOSTS:
        raise ValueError(f"unknown host: {host}")
    return _HOSTS[host]


# ---------------------------------------------------------------------------
# 入口结论
# ---------------------------------------------------------------------------


def _entry_conclusion(entry: Entry, warn_days: int, changed: Optional[bool] = None) -> dict:
    findings = evaluate(entry, warn_days=warn_days)
    conclusion = {
        "entry_id": entry.entry_id,
        "address": entry.address,
        "port": entry.port,
        "scheme": entry.scheme,
        "days_left": entry.days_left,
        "expire_soon": entry.days_left is not None
        and 0 < entry.days_left <= warn_days,
        "issuer_chain": list(entry.issuer_chain),
        "protocol": entry.protocol,
        "cipher": entry.cipher,
        "redirect": {
            "status": entry.redirect,
            "target": entry.redirect_target,
        },
        "weak": bool(findings),
        "weak_count": len(findings),
        "weak_items": [item.to_dict() for item in findings],
    }
    if changed is not None:
        conclusion["changed"] = changed
    return conclusion


def _host_result(host: str, warn_days: int) -> dict:
    entries = _entries(host)
    results = [_entry_conclusion(entry, warn_days) for entry in entries]
    weak_entries = [item["entry_id"] for item in results if item["weak"]]
    expiring_entries = [item["entry_id"] for item in results if item["expire_soon"]]
    expired_entries = [
        item["entry_id"] for item in results if item["days_left"] is not None and item["days_left"] <= 0
    ]
    return {
        "host": host,
        "endpoint_count": len(results),
        "entries": results,
        "weak": weak_entries,
        "expiring": expiring_entries,
        "expired": expired_entries,
        "weak_count": sum(item["weak_count"] for item in results),
    }


# ---------------------------------------------------------------------------
# 公开 API：check / report / expiring
# ---------------------------------------------------------------------------


def check(host: str, warn_days: int = DEFAULT_WARN_DAYS) -> dict:
    """核查一个域名的全部入口，每个入口单独给结论。"""
    return _host_result(host, warn_days)


def expiring(within: int = DEFAULT_WARN_DAYS) -> dict:
    """到期提醒：剩余天数不超过 within 的入口，按剩余天数升序，提前给出天数。"""
    rows = []
    for host in sorted(_HOSTS):
        for entry in _HOSTS[host]:
            if entry.days_left is not None and entry.days_left <= within:
                rows.append(
                    {
                        "host": host,
                        "entry_id": entry.entry_id,
                        "days_left": entry.days_left,
                        "expired": entry.days_left <= 0,
                    }
                )
    rows.sort(key=lambda row: (row["days_left"], row["host"], row["entry_id"]))
    return {
        "within": within,
        "hosts": sorted({row["host"] for row in rows}),
        "entries": rows,
    }


def _flatten_weak(warn_days: int) -> list[dict]:
    flat = []
    for host in sorted(_HOSTS):
        for entry in _HOSTS[host]:
            for item in evaluate(entry, warn_days=warn_days):
                flat.append(
                    {
                        "host": host,
                        "entry_id": entry.entry_id,
                        **item.to_dict(),
                    }
                )
    flat.sort(
        key=lambda row: (
            SEVERITY_RANK[row["severity"]],
            row["host"],
            row["entry_id"],
            row["category"],
        )
    )
    return flat


def report(warn_days: int = DEFAULT_WARN_DAYS) -> dict:
    """汇总全部域名：入口清单、证书状态、协议与套件、弱项与到期提醒。"""
    hosts = []
    total_endpoints = 0
    weak_entry_count = 0
    for host in sorted(_HOSTS):
        result = _host_result(host, warn_days)
        hosts.append(result)
        total_endpoints += result["endpoint_count"]
        weak_entry_count += len(result["weak"])

    weak_items = _flatten_weak(warn_days)
    expiry = expiring(warn_days)
    return {
        "hosts": [result["host"] for result in hosts],
        "endpoints": total_endpoints,
        "by_host": hosts,
        # 兼容旧字段名
        "weak": sorted({row["host"] for row in weak_items}),
        "weak_entries": weak_entry_count,
        "weak_items": weak_items,
        "severity_summary": {
            severity: sum(1 for row in weak_items if row["severity"] == severity)
            for severity in SEVERITY_ORDER
        },
        "expiring": expiry["hosts"],
        "expiring_entries": expiry["entries"],
    }


# ---------------------------------------------------------------------------
# 入口对比：同一域名多个入口的配置差异
# ---------------------------------------------------------------------------

_DIFF_FIELDS = (
    "address",
    "port",
    "scheme",
    "days_left",
    "issuer_chain",
    "protocol",
    "cipher",
    "redirect",
    "redirect_target",
)


def _normalize(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, tuple):
        return list(value)
    return value


def diff_entries(host: str) -> dict:
    """对比同一域名下各入口的配置，只返回取值不一致的字段。"""
    entries = _entries(host)
    diffs = {}
    for field_name in _DIFF_FIELDS:
        raw_values = {getattr(entry, field_name) for entry in entries}
        if len(raw_values) > 1:
            diffs[field_name] = {
                entry.entry_id: _normalize(getattr(entry, field_name)) for entry in entries
            }
    return {
        "host": host,
        "entry_ids": [entry.entry_id for entry in entries],
        "differences": diffs,
        "consistent": not diffs,
    }


# ---------------------------------------------------------------------------
# 增量重查：只重查配置指纹变化的入口
# ---------------------------------------------------------------------------


class Checker:
    """带缓存的核查器：首次 check 建立基线，之后 recheck 只看变化的入口。"""

    def __init__(self, hosts: Optional[dict[str, list[Entry]]] = None):
        self._hosts = hosts if hosts is not None else _HOSTS
        # (host, entry_id) -> {"fingerprint": str, "conclusion": dict}
        self._cache: dict[tuple[str, str], dict] = {}

    def check(self, host: str, warn_days: int = DEFAULT_WARN_DAYS) -> dict:
        if host not in self._hosts:
            raise ValueError(f"unknown host: {host}")
        results = []
        for entry in self._hosts[host]:
            conclusion = _entry_conclusion(entry, warn_days, changed=False)
            self._cache[(host, entry.entry_id)] = {
                "fingerprint": entry.fingerprint(),
                "conclusion": conclusion,
            }
            results.append(conclusion)
        return self._assemble(host, results, warn_days, rechecked=[e.entry_id for e in self._hosts[host]])

    def recheck(
        self,
        host: Optional[str] = None,
        warn_days: int = DEFAULT_WARN_DAYS,
        force: bool = False,
    ) -> dict:
        """重查：指纹未变的入口直接复用上次结论，变化或新增的入口重新评估。"""
        targets = [host] if host is not None else sorted(self._hosts)
        unknown = [name for name in targets if name not in self._hosts]
        if unknown:
            raise ValueError(f"unknown host: {unknown[0]}")

        results, rechecked, cached, changed, new = [], [], [], [], []
        for name in targets:
            for entry in self._hosts[name]:
                key = (name, entry.entry_id)
                fingerprint = entry.fingerprint()
                baseline = self._cache.get(key)
                if not force and baseline is not None and baseline["fingerprint"] == fingerprint:
                    conclusion = dict(baseline["conclusion"])
                    conclusion["changed"] = False
                    cached.append(entry.entry_id)
                else:
                    is_new = baseline is None
                    conclusion = _entry_conclusion(entry, warn_days, changed=not is_new)
                    self._cache[key] = {"fingerprint": fingerprint, "conclusion": conclusion}
                    rechecked.append(entry.entry_id)
                    if is_new:
                        new.append(entry.entry_id)
                    else:
                        changed.append(entry.entry_id)
                results.append(conclusion)

        return {
            "hosts": targets,
            "endpoint_count": len(results),
            "entries": results,
            "rechecked": rechecked,
            "cached": cached,
            "changed": changed,
            "new": new,
        }

    @staticmethod
    def _assemble(host: str, results: list[dict], warn_days: int, rechecked: list[str]) -> dict:
        return {
            "host": host,
            "endpoint_count": len(results),
            "entries": results,
            "weak": [row["entry_id"] for row in results if row["weak"]],
            "expiring": [row["entry_id"] for row in results if row["expire_soon"]],
            "expired": [
                row["entry_id"]
                for row in results
                if row["days_left"] is not None and row["days_left"] <= 0
            ],
            "weak_count": sum(row["weak_count"] for row in results),
            "rechecked": rechecked,
            "cached": [],
            "changed": [],
            "new": rechecked,
        }


_DEFAULT_CHECKER = Checker()


def recheck(
    host: Optional[str] = None,
    warn_days: int = DEFAULT_WARN_DAYS,
    force: bool = False,
) -> dict:
    """模块级增量重查，复用同一缓存；整改后只重查发生变化的入口。"""
    return _DEFAULT_CHECKER.recheck(host, warn_days=warn_days, force=force)


def mutate_entry(host: str, entry_id: str, **changes: object) -> Entry:
    """测试/演练用：修改登记入口的配置，返回新的 Entry（不可变对象替换式更新）。"""
    for index, entry in enumerate(_entries(host)):
        if entry.entry_id == entry_id:
            updated = replace(entry, **changes)
            _HOSTS[host][index] = updated
            return updated
    raise ValueError(f"unknown entry: {host}/{entry_id}")
