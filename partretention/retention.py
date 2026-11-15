"""分区保留：沿血缘级联、跳过活跃租约、分批删除、失败停批可回滚。

目录服务以普通 dict 注入（dataset["catalog"] 或 plan 的 catalog 参数）：

    {
        "lineage":        {"主表": ["下游表", ...]},        # 血缘
        "partitions":     {"表": ["2025-01-01", ...]},      # 下游现有分区（缺省视同主表）
        "leases":         {"表": ["lease-id", ...]},        # 活跃租约，非空即跳过
        "schema_changed": ["表", ...],                      # 结构已变更的下游，跳过并告警
        "fail_on":        ["表:天", ...],                   # 注入删除失败（演示/测试用）
    }

计划为纯 JSON，可 save_plan 保存后 load_plan 再次执行。
"""

import json
import os

DEFAULT_BATCH_SIZE = 100
DEFAULT_MAX_DEPTH = 3


def _item(dataset, day, cascade, depth):
    return {"dataset": dataset, "day": day, "cascade": cascade, "depth": depth}


def _key(item):
    return "%s:%s" % (item["dataset"], item["day"])


def plan(dataset, catalog=None, batch_size=None, max_depth=None):
    """干跑：按天列过期分区，沿血缘标记下游，输出影响范围，不删任何数据。"""
    name = dataset.get("name", "")
    days = list(dataset.get("days", []))
    cat = catalog if catalog is not None else (dataset.get("catalog") or {})
    batch_size = int(batch_size or dataset.get("batch_size") or DEFAULT_BATCH_SIZE)
    max_depth = DEFAULT_MAX_DEPTH if max_depth is None else int(max_depth)

    lineage = cat.get("lineage", {})
    partitions = cat.get("partitions", {})
    leases = cat.get("leases", {})
    changed = set(cat.get("schema_changed", []))

    skipped = []
    warnings = []
    downstream = []
    items = []

    if leases.get(name):
        for day in days:
            skipped.append({"dataset": name, "day": day, "reason": "active_lease"})
        expired = []
    else:
        expired = days
        items.extend(_item(name, day, False, 0) for day in days)

    # 沿血缘广度优先标记下游，深度受限
    seen = {name}
    frontier = [(name, 0)]
    while frontier:
        current, depth = frontier.pop(0)
        children = lineage.get(current, [])
        if depth >= max_depth:
            if children:
                warnings.append(
                    "级联深度达上限 %d：%s 的下游 %s 未标记" % (max_depth, current, children))
            continue
        for child in children:
            if child in seen:
                continue
            seen.add(child)
            if leases.get(child):
                skipped.append({"dataset": child, "reason": "active_lease",
                                "leases": list(leases[child])})
                continue
            if child in changed:
                skipped.append({"dataset": child, "reason": "schema_changed"})
                warnings.append("下游 %s 结构已变更，跳过级联删除" % child)
                continue
            child_days = [d for d in partitions.get(child, days) if d in days]
            downstream.append({"name": child, "days": child_days, "depth": depth + 1})
            items.extend(_item(child, day, True, depth + 1) for day in child_days)
            frontier.append((child, depth + 1))

    impact = {}
    for it in items:
        entry = impact.setdefault(it["dataset"], {"partitions": 0, "cascade": it["cascade"]})
        entry["partitions"] += 1

    return {
        "dataset": name,
        "expired": expired,
        "downstream": downstream,
        "skipped": skipped,
        "warnings": warnings,
        "items": items,
        "batches": (len(items) + batch_size - 1) // batch_size,
        "batch_size": batch_size,
        "max_depth": max_depth,
        "fail_on": list(cat.get("fail_on", [])),
        "impact": impact,
        "dry_run": True,
    }


def apply(plan_data, dry_run=False, archive_dir=None, audit_path=None):
    """分批执行保留计划：失败即停批，删除前按天归档，结果写审计并按数据集汇总。"""
    items = plan_data.get("items")
    if items is None:  # 兼容只有 expired 的旧计划
        items = [_item(plan_data.get("dataset", ""), day, False, 0)
                 for day in plan_data.get("expired", [])]
    batch_size = int(plan_data.get("batch_size") or DEFAULT_BATCH_SIZE)
    fail_on = set(plan_data.get("fail_on", []))

    deleted = []
    cascaded = []
    archived = []
    records = []
    failed_batch = None

    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    for index, batch in enumerate(batches):
        if fail_on & {_key(it) for it in batch}:
            records.append({"index": index, "items": batch, "status": "failed"})
            failed_batch = index
            break  # 失败停批：后续批次不再执行
        status = "dry_run" if dry_run else "deleted"
        if not dry_run:
            for it in batch:
                if archive_dir:
                    archived.append(_archive(archive_dir, it))
                if it["cascade"]:
                    cascaded.append(_key(it))
                else:
                    deleted.append(it["day"])
        records.append({"index": index, "items": batch, "status": status})

    skipped = list(plan_data.get("skipped", []))
    summary = _summary(plan_data.get("dataset", ""), records, skipped)
    result = {
        "deleted": deleted,
        "cascaded": cascaded,
        "skipped": skipped,
        "batches": len(records),
        "batch_records": records,
        "failed_batch": failed_batch,
        "rolled_back": [],
        "archived": archived,
        "warnings": list(plan_data.get("warnings", [])),
        "audit_summary": summary,
        "dry_run": dry_run,
    }
    if audit_path and not dry_run:
        _audit(audit_path, plan_data.get("dataset", ""), result)
    return result


def rollback(result, batch_index):
    """单独回滚某个批次（通常是失败批次），返回更新后的结果。"""
    record = None
    for rec in result.get("batch_records", []):
        if rec["index"] == batch_index:
            record = rec
            break
    if record is None:
        raise KeyError("批次 %r 不存在" % (batch_index,))
    if record["status"] == "rolled_back":
        raise ValueError("批次 %r 已回滚" % (batch_index,))
    for it in record["items"]:
        if it["cascade"]:
            if _key(it) in result["cascaded"]:
                result["cascaded"].remove(_key(it))
        elif it["day"] in result["deleted"]:
            result["deleted"].remove(it["day"])
        result["rolled_back"].append(_key(it))
    record["status"] = "rolled_back"
    return result


def save_plan(plan_data, path):
    """保存计划为 JSON，之后可 load_plan 再次执行。"""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(plan_data, fh, ensure_ascii=False, indent=2)
    return path


def load_plan(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _archive(archive_dir, item):
    """按天归档：archive_dir/<天>/<表>.json。"""
    path = os.path.join(archive_dir, str(item["day"]), item["dataset"] + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(item, fh, ensure_ascii=False)
    return path


def _summary(name, records, skipped):
    summary = {}
    for rec in records:
        if rec["status"] != "deleted":
            continue
        for it in rec["items"]:
            entry = summary.setdefault(it["dataset"], {"deleted": 0, "cascaded": 0, "skipped": 0})
            entry["cascaded" if it["cascade"] else "deleted"] += 1
    for sk in skipped:
        entry = summary.setdefault(sk["dataset"], {"deleted": 0, "cascaded": 0, "skipped": 0})
        entry["skipped"] += 1
    summary.setdefault(name, {"deleted": 0, "cascaded": 0, "skipped": 0})
    return summary


def _audit(audit_path, name, result):
    record = {
        "dataset": name,
        "batches": result["batches"],
        "failed_batch": result["failed_batch"],
        "summary": result["audit_summary"],
    }
    with open(audit_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
