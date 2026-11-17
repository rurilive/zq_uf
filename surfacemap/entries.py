"""攻击面入口清单。

一个入口按「域名 + 地址 + 端口」合并成一条，重复登记只补充来源和确认时间。
入口带三种状态：

* ``active``  —— 归属明确、在线
* ``pending`` —— 归属无法确认（未登记归属或多个归属互相矛盾）
* ``offline`` —— 已下线，保留 ``offline_at``，重新登记可恢复

失效入口不删除，长期没有重新确认的入口通过 ``report(stale_days=...)``
单独列出；清单变化写入变更日志，可按月筛选或单独导出。
"""

import json
import re
from datetime import date, timedelta

_ROWS = {}
_CHANGES = []

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

DEFAULT_PORT = 443
DEFAULT_STALE_DAYS = 90


def reset():
    """清空已登记的入口和变更日志。"""
    _ROWS.clear()
    _CHANGES.clear()


def _today():
    return date.today().isoformat()


def _as_date(value, field):
    """把日期统一成 YYYY-MM-DD，非法值直接报错，避免脏数据混进清单。"""
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if len(text) > 10:
        text = text[:10]
    if not _DATE_RE.match(text):
        raise ValueError("%s must be a date like 2026-11-17" % field)
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ValueError("%s must be a real date" % field) from None
    return text


def _key(domain, address, port):
    return "%s|%s|%s" % (domain.lower(), port, address.lower())


def _owner(entry):
    return entry["owners"][0] if len(entry["owners"]) == 1 else "unknown"


def _status(entry):
    if entry["offline_at"]:
        return "offline"
    return "active" if len(entry["owners"]) == 1 else "pending"


def _public(entry):
    status = _status(entry)
    return {
        "domain": entry["domain"],
        "address": entry["address"],
        "port": entry["port"],
        "owner": _owner(entry),
        "owners": list(entry["owners"]),
        "third_party": entry["third_party"],
        "status": status,
        "sources": list(entry["sources"]),
        "source": entry["sources"][0] if entry["sources"] else "manual",
        "first_seen": entry["first_seen"],
        "last_seen": entry["last_seen"],
        "confirmed_at": entry["confirmed_at"],
        "offline_at": entry["offline_at"],
    }


def _log(when, action, entry, source, owner=None):
    _CHANGES.append({
        "date": when,
        "action": action,
        "domain": entry["domain"],
        "address": entry["address"],
        "port": entry["port"],
        "owner": owner if owner is not None else _owner(entry),
        "source": source,
    })


def _normalize(raw):
    if not isinstance(raw, dict):
        raise ValueError("entry must be a dict")
    domain = str(raw.get("domain") or "").strip()
    address = str(raw.get("address") or "").strip()
    if not domain or not address:
        raise ValueError("entry needs domain and address")
    try:
        port = int(raw.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        raise ValueError("port must be an integer") from None
    return {
        "domain": domain,
        "address": address,
        "port": port,
        "owner": str(raw.get("owner") or "").strip(),
        "source": str(raw.get("source") or "manual").strip() or "manual",
        "offline": bool(raw.get("offline", False)),
        "third_party": bool(raw.get("third_party", False)),
        "seen": _as_date(raw.get("seen_at") or raw.get("date") or _today(), "seen_at"),
        "first_seen": raw.get("first_seen"),
    }


def surface(entries):
    """登记一批入口；同一入口重复登记会合并，不新增行。

    返回本次登记的计数：``added``（新入口）、``merged``（合并到已有入口）、
    ``offline``（本次转失效）、``restored``（本次恢复上线）、``total``（入口总数）。
    """
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")

    counts = {"added": 0, "merged": 0, "offline": 0, "restored": 0}
    for raw in entries:
        item = _normalize(raw)
        key = _key(item["domain"], item["address"], item["port"])
        entry = _ROWS.get(key)

        if entry is None:
            first_seen = item["first_seen"] or item["seen"]
            entry = {
                "domain": item["domain"],
                "address": item["address"],
                "port": item["port"],
                "owners": [item["owner"]] if item["owner"] else [],
                "third_party": item["third_party"],
                "sources": [item["source"]],
                "first_seen": _as_date(first_seen, "first_seen"),
                "last_seen": item["seen"],
                "confirmed_at": item["seen"],
                "offline_at": item["seen"] if item["offline"] else None,
            }
            _ROWS[key] = entry
            counts["added"] += 1
            _log(item["seen"], "added", entry, item["source"],
                 owner=item["owner"] or "unknown")
            continue

        counts["merged"] += 1
        if item["source"] not in entry["sources"]:
            entry["sources"].append(item["source"])
            _log(item["seen"], "source", entry, item["source"])
        if item["third_party"] and not entry["third_party"]:
            entry["third_party"] = True
            _log(item["seen"], "third_party", entry, item["source"])
        if item["owner"] and item["owner"] not in entry["owners"]:
            entry["owners"].append(item["owner"])
            _log(item["seen"], "owner", entry, item["source"], owner=item["owner"])

        first_seen = _as_date(item["first_seen"] or item["seen"], "first_seen")
        if first_seen < entry["first_seen"]:
            entry["first_seen"] = first_seen
        if item["seen"] > entry["last_seen"]:
            entry["last_seen"] = item["seen"]
        if item["seen"] > entry["confirmed_at"]:
            entry["confirmed_at"] = item["seen"]

        if item["offline"]:
            if entry["offline_at"] is None:
                entry["offline_at"] = item["seen"]
                counts["offline"] += 1
                _log(item["seen"], "offline", entry, item["source"])
        elif entry["offline_at"] is not None:
            entry["offline_at"] = None
            counts["restored"] += 1
            _log(item["seen"], "restored", entry, item["source"])

    counts["total"] = len(_ROWS)
    return counts


def changes(month=None):
    """返回变更日志；``month`` 为 ``YYYY-MM`` 时只返回当月变化。"""
    if month is None:
        return [dict(item) for item in _CHANGES]
    if not _MONTH_RE.match(str(month)):
        raise ValueError("month must be YYYY-MM")
    return [dict(item) for item in _CHANGES if item["date"][:7] == month]


def monthly_changes():
    """按月汇总变更动作，便于相邻月份对比。"""
    summary = {}
    for item in _CHANGES:
        bucket = summary.setdefault(item["date"][:7], {})
        bucket[item["action"]] = bucket.get(item["action"], 0) + 1
    return summary


def export_changes(path=None, month=None):
    """导出变更日志（JSON 可序列化）；给了 ``path`` 时同时写入文件。"""
    data = changes(month)
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    return data


def report(owner=None, month=None, as_of=None, stale_days=DEFAULT_STALE_DAYS):
    """返回入口清单视图。

    * ``active`` / ``offline`` / ``pending`` —— 有效、失效、待确认入口列表
    * ``third_party`` —— 第三方托管入口
    * ``stale`` —— 超过 ``stale_days`` 未重新确认的在线入口
    * ``sources`` —— 来源分布（一个入口的每个来源各计一次）
    * ``by_owner`` —— 按业务归属分组（失效入口不计入）
    * ``entries`` —— 合并后的全部入口
    * ``changes`` —— 变更日志，可按 ``month`` 筛选

    ``owner`` 用于按业务筛选；``as_of`` 可固定日期用于回溯或测试。
    """
    if int(stale_days) < 0:
        raise ValueError("stale_days must be >= 0")
    today = _as_date(as_of or _today(), "as_of")
    cutoff = (date.fromisoformat(today) - timedelta(days=int(stale_days))).isoformat()

    rows = [_public(entry) for entry in sorted(
        _ROWS.values(), key=lambda e: (e["domain"], e["port"], e["address"]),
    )]
    if owner is not None:
        rows = [row for row in rows if row["owner"] == owner]

    active = [row for row in rows if row["status"] == "active"]
    offline = [row for row in rows if row["status"] == "offline"]
    pending = [row for row in rows if row["status"] == "pending"]
    third_party = [row for row in rows if row["third_party"]]
    stale = [
        row for row in rows
        if row["status"] != "offline" and row["confirmed_at"] < cutoff
    ]

    sources = {}
    for row in rows:
        for source in row["sources"]:
            sources[source] = sources.get(source, 0) + 1

    by_owner = {}
    for row in rows:
        if row["status"] != "offline":
            by_owner[row["owner"]] = by_owner.get(row["owner"], 0) + 1

    change_rows = changes(month)
    if owner is not None:
        change_rows = [item for item in change_rows if item["owner"] == owner]

    return {
        "total": len(rows),
        "counts": {
            "active": len(active),
            "offline": len(offline),
            "pending": len(pending),
        },
        "active": active,
        "offline": offline,
        "pending": pending,
        "third_party": third_party,
        "stale": stale,
        "sources": sources,
        "by_owner": by_owner,
        "entries": rows,
        "changes": change_rows,
    }
