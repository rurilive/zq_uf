"""血缘推断与人工确认。

从任务日志的读写记录中，按同任务、同批次归集读字段和写字段，
推断 read -> write 血缘边，附带置信度与证据；与人工登记冲突时
两侧保留并标记待确认。confirm 写回登记并记审计，rollback 可回滚。
仅使用 Python 标准库。
"""

from datetime import datetime, timezone

# 置信度口径：
# - 同一条边被 >=2 个不同批次的日志支持（同任务跨批次复现）：高置信度
# - 同任务同批次内 >=2 条日志重复支持：中置信度
# - 仅 1 条日志支持：证据不足，不产出推断边
HIGH_CONFIDENCE = 0.9
MEDIUM_CONFIDENCE = 0.7
MIN_EVIDENCE_RECORDS = 2
DEFAULT_BATCH = "default"
UNKNOWN_JOB = "unknown"

# 人工登记 / 确认后的血缘边：edge -> 登记记录
_registry = {}
# 审计流水，只追加
_audit = []
_audit_seq = 0
# 最近一次 infer 产出的推断结果，供 confirm 附带证据快照
_latest_inference = {}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _next_audit_id():
    global _audit_seq
    _audit_seq += 1
    return _audit_seq


def _as_list(value):
    """read/write 字段兼容字符串或列表。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _normalize_edge(edge):
    if isinstance(edge, dict) and "edge" in edge:
        edge = edge["edge"]
    source, target = edge
    return (str(source), str(target))


def _group_logs(logs):
    """按 (job, batch) 归集读集合、写集合和支撑日志下标。"""
    groups = {}
    for index, log in enumerate(logs):
        if not isinstance(log, dict):
            continue
        reads = _as_list(log.get("read"))
        writes = _as_list(log.get("write"))
        if not reads or not writes:
            continue
        job = log.get("job") or UNKNOWN_JOB
        batch = log.get("batch") or DEFAULT_BATCH
        group = groups.setdefault(
            (job, batch),
            {"reads": set(), "writes": set(), "records": []},
        )
        group["reads"].update(reads)
        group["writes"].update(writes)
        group["records"].append(index)
    return groups


def infer(logs):
    """从任务日志推断血缘边。

    返回推断记录列表，每条记录包含：
    edge: (source, target)；confidence/level: 置信度；
    evidence: 按任务、批次列出的证据；status: pending|conflict；
    conflicts_with: 冲突的已登记边。
    证据不足（仅 1 条日志支持）或已精确登记的边不产出。
    """
    groups = _group_logs(logs)
    candidates = {}
    for (job, batch), group in groups.items():
        for source in sorted(group["reads"]):
            for target in sorted(group["writes"]):
                if source == target:
                    continue
                edge = (source, target)
                item = candidates.setdefault(
                    edge,
                    {"record_count": 0, "batches": set(), "evidence": []},
                )
                item["record_count"] += len(group["records"])
                item["batches"].add(batch)
                item["evidence"].append(
                    {
                        "job": job,
                        "batch": batch,
                        "record_count": len(group["records"]),
                        "log_indexes": list(group["records"]),
                    }
                )

    results = []
    _latest_inference.clear()
    for edge in sorted(candidates):
        item = candidates[edge]
        if item["record_count"] < MIN_EVIDENCE_RECORDS:
            continue
        if edge in _registry:
            # 已有登记优先：精确命中的边不再作为推断结果产出
            continue
        if len(item["batches"]) >= 2:
            confidence, level = HIGH_CONFIDENCE, "high"
        else:
            confidence, level = MEDIUM_CONFIDENCE, "medium"
        conflicts_with = sorted(
            registered
            for registered in _registry
            if registered[0] == edge[0] and registered[1] != edge[1]
        )
        record = {
            "edge": edge,
            "confidence": confidence,
            "level": level,
            "status": "conflict" if conflicts_with else "pending",
            "conflicts_with": conflicts_with,
            "evidence": sorted(item["evidence"], key=lambda e: (e["job"], e["batch"])),
        }
        results.append(record)
        _latest_inference[edge] = dict(record)
    return results


def _write_registry(edge, operator, source, action, evidence):
    """写回登记并追加审计，返回 (登记记录, 审计条目)。"""
    before = _registry.get(edge)
    conflicts_with = sorted(
        registered
        for registered in _registry
        if registered[0] == edge[0] and registered[1] != edge[1]
    )
    record = {
        "edge": edge,
        "source": source,
        "operator": operator,
        "at": _now(),
        "evidence": evidence if evidence is not None else (before or {}).get("evidence"),
        "conflicts_with": conflicts_with,
    }
    _registry[edge] = record
    entry = {
        "id": _next_audit_id(),
        "action": action,
        "edge": edge,
        "operator": operator,
        "at": record["at"],
        "before": before,
        "after": dict(record),
        "conflicts_with": conflicts_with,
        "rolled_back": False,
    }
    _audit.append(entry)
    return record, entry


def register(edge, operator="manual"):
    """登记人工维护的既有血缘边（登记优先于推断）。"""
    edge = _normalize_edge(edge)
    record, _ = _write_registry(edge, operator, "registered", "register", None)
    return record


def confirm(edge, operator):
    """人工确认推断边：写回登记并记录审计，冲突时两侧保留。"""
    edge = _normalize_edge(edge)
    evidence = _latest_inference.get(edge, {}).get("evidence")
    record, entry = _write_registry(edge, operator, "confirmed", "confirm", evidence)
    return {
        "edge": edge,
        "confirmed": True,
        "operator": operator,
        "at": record["at"],
        "status": "confirmed",
        "conflicts_with": entry["conflicts_with"],
        "audit_id": entry["id"],
    }


def rollback(edge, operator):
    """回滚最近一次作用于该边的 confirm/register，恢复登记并记审计。"""
    edge = _normalize_edge(edge)
    target = None
    for entry in reversed(_audit):
        if (
            entry["edge"] == edge
            and entry["action"] in ("confirm", "register")
            and not entry["rolled_back"]
        ):
            target = entry
            break
    if target is None:
        return {"edge": edge, "rolled_back": False, "reason": "nothing_to_rollback"}
    target["rolled_back"] = True
    if target["before"] is None:
        _registry.pop(edge, None)
    else:
        _registry[edge] = target["before"]
    revert = {
        "id": _next_audit_id(),
        "action": "rollback",
        "edge": edge,
        "operator": operator,
        "at": _now(),
        "reverts": target["id"],
    }
    _audit.append(revert)
    return {
        "edge": edge,
        "rolled_back": True,
        "audit_id": revert["id"],
        "reverts": target["id"],
    }


def registry():
    """返回当前登记的快照。"""
    return {edge: dict(record) for edge, record in _registry.items()}


def audit():
    """返回审计流水的快照。"""
    return [dict(entry) for entry in _audit]


def reset():
    """清空登记、审计和推断缓存，主要用于测试隔离。"""
    global _audit_seq
    _registry.clear()
    _audit.clear()
    _latest_inference.clear()
    _audit_seq = 0
