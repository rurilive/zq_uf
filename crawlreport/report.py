"""采集报告对账模块。

口径：
- build(batch) 统计总数，按站点/状态/阶段分桶，失败与跳过分开；
- reconcile(report) 将报告中的应入库行（ok 且带 id）与入库行按 id 对账，
  比较行数与校验和，差异给出明细并按 (站点, 阶段, id) 排序、按原因归类；
- 无 id 的行无法对账，单列，不参与差异。
"""

import hashlib
import json
from datetime import datetime, timezone

REPORT_VERSION = "1.0"

_OK = {"ok", "success", "succeeded", "done"}
_FAILED = {"fail", "failed", "error"}
_SKIPPED = {"skip", "skipped", "ignore", "ignored"}
_STATUSES = ("ok", "failed", "skipped", "unknown")

_ID_KEYS = ("id", "url", "key")


def _classify(status):
    s = str(status or "").strip().lower()
    if s in _OK:
        return "ok"
    if s in _FAILED:
        return "failed"
    if s in _SKIPPED:
        return "skipped"
    return "unknown"


def _row_id(row):
    for key in _ID_KEYS:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _checksum(payload):
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _normalize(row):
    """字典行 -> 规范条目；非字典行返回 None（只计总数）。"""
    if not isinstance(row, dict):
        return None
    entry = {
        "id": _row_id(row),
        "site": str(row.get("site") or ""),
        "stage": str(row.get("stage") or ""),
        "status": _classify(row.get("status")),
        "reason": str(row.get("reason") or ""),
        "time": str(row.get("time") or row.get("ts") or ""),
    }
    entry["checksum"] = _checksum(
        {k: entry[k] for k in ("id", "site", "stage", "status", "reason", "time")}
    )
    return entry


def _normalize_rows(rows):
    out = []
    for row in rows or []:
        entry = _normalize(row)
        if entry is not None:
            out.append(entry)
    return sorted(out, key=_sort_key)


def _sort_key(entry):
    return (entry.get("site", ""), entry.get("stage", ""), entry.get("id") or "")


def _bucket():
    return {"total": 0, "ok": 0, "failed": 0, "skipped": 0, "unknown": 0}


def _bump(bucket, status):
    bucket["total"] += 1
    bucket[status] = bucket.get(status, 0) + 1


def _dedup(entries):
    """按 id 去重，后者覆盖前者；无 id 条目全部保留。"""
    by_id = {}
    order = []
    for entry in entries:
        if not entry["id"]:
            order.append(entry)
            continue
        if entry["id"] in by_id:
            order[by_id[entry["id"]]] = entry
        else:
            by_id[entry["id"]] = len(order)
            order.append(entry)
    return order


def _now_iso(now):
    if now is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    if isinstance(now, datetime):
        return now.isoformat(timespec="seconds")
    return str(now)


def build(batch, resume_from=None, now=None):
    """生成统计报告。

    resume_from: 上一次中断时生成的报告，合并其 checkpoint 后续算；
    now: 固定生成时间（默认当前 UTC 时间），同一批次同一 now 结果一致。
    """
    rows = list(batch.get("rows", []))
    entries = []
    total = 0
    unparsed = 0
    if resume_from is not None:
        entries.extend(resume_from.get("checkpoint", []))
        total += int(resume_from.get("total", 0))
        unparsed += int(resume_from.get("unparsed", 0))
    total += len(rows)
    unparsed += sum(1 for row in rows if not isinstance(row, dict))
    for row in rows:
        entry = _normalize(row)
        if entry is not None:
            entries.append(entry)
    entries = _dedup(entries)

    by_status = {status: 0 for status in _STATUSES}
    by_site = {}
    by_stage = {}
    by_reason = {}
    for entry in entries:
        by_status[entry["status"]] += 1
        if entry["site"]:
            _bump(by_site.setdefault(entry["site"], _bucket()), entry["status"])
        if entry["stage"]:
            _bump(by_stage.setdefault(entry["stage"], _bucket()), entry["status"])
        if entry["reason"]:
            by_reason[entry["reason"]] = by_reason.get(entry["reason"], 0) + 1

    ordered = sorted(entries, key=_sort_key)
    reconcilable = [e for e in ordered if e["id"] and e["status"] == "ok"]
    report = {
        "version": REPORT_VERSION,
        "batch": batch.get("id", ""),
        "generated_at": _now_iso(now),
        "total": total,
        "unparsed": unparsed,
        "by_status": by_status,
        "by_site": {k: by_site[k] for k in sorted(by_site)},
        "by_stage": {k: by_stage[k] for k in sorted(by_stage)},
        "by_reason": {k: by_reason[k] for k in sorted(by_reason)},
        "failures": [e for e in ordered if e["status"] == "failed"],
        "skips": [e for e in ordered if e["status"] == "skipped"],
        "unreconcilable": [e for e in ordered if not e["id"]],
        "row_count": len(reconcilable),
        "checksum": _checksum([e["checksum"] for e in reconcilable]),
        "checkpoint": ordered,
    }
    if "stored" in batch:
        stored = _normalize_rows(batch.get("stored"))
        report["stored_rows"] = stored
        report["stored_row_count"] = len([e for e in stored if e["id"]])
        report["stored_checksum"] = _checksum(
            [e["checksum"] for e in stored if e["id"]]
        )
    return report


def _diff_entry(entry, reason, detail):
    return {
        "id": entry.get("id"),
        "site": entry.get("site", ""),
        "stage": entry.get("stage", ""),
        "reason": reason,
        "detail": detail,
    }


def reconcile(report, stored=None):
    """对账：报告应入库行 vs 实际入库行。

    stored: 入库行（字典行列表）；缺省用报告里的 stored_rows；
    都没有时按入库为空处理。差异按 (站点, 阶段, id) 排序，按原因归类计数；
    无 id 的行不参与对账，单列在 unreconcilable。
    """
    expected = sorted(
        (
            e
            for e in report.get("checkpoint", [])
            if e.get("id") and e.get("status") == "ok"
        ),
        key=_sort_key,
    )
    if stored is not None:
        actual = _normalize_rows(stored)
    else:
        actual = list(report.get("stored_rows", []))
    actual_reconcilable = sorted((e for e in actual if e.get("id")), key=_sort_key)

    unreconcilable = list(report.get("unreconcilable", []))
    unreconcilable += [e for e in actual if not e.get("id")]

    expected_by_id = {e["id"]: e for e in expected}
    actual_by_id = {e["id"]: e for e in actual_reconcilable}
    diff = []
    for rid, entry in expected_by_id.items():
        other = actual_by_id.get(rid)
        if other is None:
            diff.append(_diff_entry(entry, "missing_in_store", "报告应入库但入库缺失"))
        elif other["checksum"] != entry["checksum"]:
            diff.append(_diff_entry(entry, "checksum_mismatch", "报告与入库内容不一致"))
    for rid, entry in actual_by_id.items():
        if rid not in expected_by_id:
            diff.append(_diff_entry(entry, "extra_in_store", "入库有而报告无"))
    diff.sort(key=lambda d: (d["site"], d["stage"], d["id"] or ""))

    by_reason = {}
    for entry in diff:
        by_reason[entry["reason"]] = by_reason.get(entry["reason"], 0) + 1

    expected_checksum = _checksum([e["checksum"] for e in expected])
    actual_checksum = _checksum([e["checksum"] for e in actual_reconcilable])
    count_matched = len(expected) == len(actual_reconcilable)
    checksum_matched = expected_checksum == actual_checksum

    return {
        "batch": report.get("batch", ""),
        "matched": not diff and count_matched and checksum_matched,
        "row_count": {
            "expected": len(expected),
            "actual": len(actual_reconcilable),
            "matched": count_matched,
        },
        "checksum": {
            "expected": expected_checksum,
            "actual": actual_checksum,
            "matched": checksum_matched,
        },
        "diff": diff,
        "by_reason": by_reason,
        "unreconcilable": unreconcilable,
    }


def drilldown(report, site=None, stage=None, reason=None, status=None,
              since=None, until=None):
    """按站点/阶段/原因/状态/时间范围下钻报告明细，按 (站点, 阶段, id) 排序。"""
    out = []
    for entry in report.get("checkpoint", []):
        if site is not None and entry.get("site") != site:
            continue
        if stage is not None and entry.get("stage") != stage:
            continue
        if reason is not None and entry.get("reason") != reason:
            continue
        if status is not None and entry.get("status") != status:
            continue
        time = entry.get("time", "")
        if since is not None and (not time or time < since):
            continue
        if until is not None and (not time or time > until):
            continue
        out.append(entry)
    return sorted(out, key=_sort_key)


def export(report, path=None):
    """导出报告为 JSON 文本（含版本号和生成时间），可选写入文件。"""
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return text
