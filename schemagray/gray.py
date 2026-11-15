"""结构变更灰度：按表、任务和读写方向分批，逐批观察，异常停批并回滚已放量表。"""

DEFAULT_THRESHOLDS = {"error_rate": 0.01, "latency_ms": 500.0}
DEFAULT_BATCH_SIZE = 2


def _normalize_tables(change):
    tables = []
    for item in change.get("tables", []):
        if isinstance(item, str):
            item = {"name": item}
        tables.append({
            "name": item["name"],
            "access": item.get("access", item.get("mode", "write")),
            "task": item.get("task", "default"),
        })
    return tables


def plan(change):
    """只读表先灰度，确认下游查询不受影响后再推写入表；同方向内按 batch_size 切批。"""
    tables = _normalize_tables(change)
    size = max(1, int(change.get("batch_size") or DEFAULT_BATCH_SIZE))
    reads = [t for t in tables if t["access"] == "read"]
    writes = [t for t in tables if t["access"] != "read"]
    batches = []
    for group in (reads, writes):
        for i in range(0, len(group), size):
            batches.append([t["name"] for t in group[i:i + size]])
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(change.get("thresholds") or {})
    return {
        "tables": [t["name"] for t in tables],
        "batches": batches,
        "thresholds": thresholds,
        "approver": change.get("approver"),
        "summary": change.get("summary"),
    }


def apply(plan_data, observe=None, resume_from=None):
    """逐批放量并观察错误率/延迟；异常停批并回滚已放量表，保留变更记录与审批信息。

    observe(batch, batch_index) -> {"error_rate": float, "latency_ms": float}，
    缺省视为健康。停批后把返回的 plan（带 resume_from）再次传入即可接着上一批继续。
    """
    batches = plan_data.get("batches") or [list(plan_data.get("tables", []))]
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(plan_data.get("thresholds") or {})
    start = resume_from if resume_from is not None else int(plan_data.get("resume_from", 0))

    applied, rolled_back, observations = [], [], []
    paused, stop_reason = False, None
    cursor = start
    for idx in range(start, len(batches)):
        batch = list(batches[idx])
        metrics = observe(batch, idx) if observe else {}
        metrics = {"error_rate": 0.0, "latency_ms": 0.0, **(metrics or {})}
        observations.append({"batch": idx, "tables": batch, **metrics})
        if (metrics["error_rate"] > thresholds["error_rate"]
                or metrics["latency_ms"] > thresholds["latency_ms"]):
            paused = True
            stop_reason = (
                "batch %d 观察异常: error_rate=%.4f latency_ms=%.1f 超过阈值 "
                "error_rate<=%.4f latency_ms<=%.1f"
                % (idx, metrics["error_rate"], metrics["latency_ms"],
                   thresholds["error_rate"], thresholds["latency_ms"])
            )
            rolled_back = applied + batch  # 回滚只影响已放量的表（含当前批）
            applied = []
            break
        applied.extend(batch)
        cursor = idx + 1

    return {
        "batches": batches,
        "observations": observations,
        "applied": applied,
        "paused": paused,
        "stop_reason": stop_reason,
        "rolled_back": rolled_back,
        "resume_from": cursor,
        "record": {
            "approver": plan_data.get("approver"),
            "summary": plan_data.get("summary"),
            "stop_reason": stop_reason,
            "rolled_back": list(rolled_back),
        },
    }
