"""指标回填：按时间和维度切片，先撤销再写，对账差异超阈值停批。"""

import hashlib
import itertools
import json

_PLANS = {}   # plan_id -> 计划，保存后可按时间范围复用
_RUNS = {}    # run_id -> 运行明细，可查询、导出、回放
_TARGET = {}  # slice_key -> {"rows": [...], "metric": float}，模拟目标表
_seq = {"plan": 0, "run": 0}


def _next(kind):
    _seq[kind] += 1
    return "%s-%d" % (kind, _seq[kind])


def _slice_key(s):
    dims = ",".join("%s=%s" % (k, v) for k, v in sorted(s["dims"].items()))
    return "%s~%s|%s" % (s["start"], s["end"], dims)


def _checksum(rows):
    h = hashlib.md5()
    for r in sorted((repr(r) for r in rows)):
        h.update(r.encode())
    return h.hexdigest()


def plan(range_data, slice_size=None, dimensions=None, threshold=0.0, save=True):
    """按时间和维度切片生成回填计划；计划保存复用，每片独立重算。"""
    start, end = range_data["start"], range_data["end"]
    step = slice_size or (end - start + 1)
    windows = [(s, min(s + step - 1, end)) for s in range(start, end + 1, step)]
    dims_list = [dict(zip(dimensions, combo)) for combo in itertools.product(*dimensions.values())] if dimensions else [{}]
    slices = []
    for s, e in windows:
        for d in dims_list:
            slices.append({"id": len(slices), "start": s, "end": e, "dims": d})
    p = {"plan_id": _next("plan"), "start": start, "end": end,
         "slice_size": step, "threshold": threshold,
         "slices": slices, "saved": save}
    if save:
        _PLANS[p["plan_id"]] = p
    return p


def _apply_slice(s, source_rows, compute, threshold, run):
    key = _slice_key(s)
    rec = {"slice_id": s["id"], "key": key, "status": "ok", "reason": None}
    try:
        old = _TARGET.get(key, {"rows": [], "metric": 0})
        rows = [r for r in source_rows
                if s["start"] <= r["time"] <= s["end"]
                and all(r.get(k) == v for k, v in s["dims"].items())]
        metric = compute(rows)
        # 先撤销旧结果再写，重复执行不会翻倍
        _TARGET[key] = {"rows": rows, "metric": metric}
        run["retracted"] += len(old["rows"])
        # 对账：回填前后行数、内容校验、和原值差异
        diff = {"rows_before": len(old["rows"]), "rows_after": len(rows),
                "checksum_before": _checksum(old["rows"]),
                "checksum_after": _checksum(rows),
                "metric_before": old["metric"], "metric_after": metric,
                "metric_diff": metric - old["metric"],
                "metric_diff_ratio": abs(metric - old["metric"]) / max(abs(old["metric"]), 1.0)}
        rec["metric_after"] = metric
        rec["diff"] = diff
        if old["rows"] and diff["metric_diff_ratio"] > threshold:
            rec["status"] = "stopped"
            rec["reason"] = "对账差异超阈值: %.4f > %.4f" % (diff["metric_diff_ratio"], threshold)
    except Exception as e:  # 失败分片记录原因
        rec["status"] = "failed"
        rec["reason"] = "%s: %s" % (type(e).__name__, e)
    return rec


def _summarize(run):
    diffs = [s["diff"] for s in run["slices"] if s.get("diff")]
    if not diffs:
        return None
    return {"max_metric_diff_ratio": max(d["metric_diff_ratio"] for d in diffs),
            "rows_before": sum(d["rows_before"] for d in diffs),
            "rows_after": sum(d["rows_after"] for d in diffs)}


def apply(plan_data, source_rows, compute=None, trial=False):
    """逐片执行：先撤销再写，每片对账，差异超阈值停批。

    trial=True 只跑第一个时间片并重复一次，验证撤销重写后指标不翻倍。
    返回分片明细、对账差异和停批原因。
    """
    compute = compute or (lambda rows: sum(r.get("value", 0) for r in rows))
    threshold = plan_data.get("threshold", 0.0)
    slices = plan_data["slices"][:1] if trial else plan_data["slices"]
    run = {"run_id": _next("run"), "plan_id": plan_data.get("plan_id"),
           "trial": trial, "slices": [], "retracted": 0,
           "diff": None, "stopped": False, "stop_reason": None}
    for s in slices:
        rec = _apply_slice(s, source_rows, compute, threshold, run)
        run["slices"].append(rec)
        if rec["status"] == "stopped":
            run["stopped"] = True
            run["stop_reason"] = rec["reason"]
            break
    if trial and not run["stopped"]:
        again = _apply_slice(slices[0], source_rows, compute, threshold, run)
        first = run["slices"][0]["metric_after"]
        run["double_check"] = {"first": first, "second": again["metric_after"],
                               "doubled": again["metric_after"] != first}
        run["slices"].append(again)
    run["diff"] = _summarize(run)
    _RUNS[run["run_id"]] = {"run": run, "plan": plan_data, "source_rows": source_rows}
    return run


def retry(run_id, slice_ids=None):
    """停批或失败后，单独重试失败分片（默认全部失败/未通过的分片）。"""
    ctx = _RUNS[run_id]
    bad = {s["slice_id"] for s in ctx["run"]["slices"] if s["status"] != "ok"}
    ids = set(slice_ids) if slice_ids is not None else bad
    plan_data = dict(ctx["plan"])
    plan_data["slices"] = [s for s in ctx["plan"]["slices"] if s["id"] in ids]
    return apply(plan_data, ctx["source_rows"])


def query(run_id):
    """查询运行状态：分片结果、对账差异、停批原因。"""
    run = _RUNS[run_id]["run"]
    return {"run_id": run["run_id"], "stopped": run["stopped"],
            "stop_reason": run["stop_reason"], "retracted": run["retracted"],
            "diff": run["diff"],
            "slices": [{"slice_id": s["slice_id"], "status": s["status"],
                        "reason": s["reason"]} for s in run["slices"]]}


def export(run_id):
    """导出运行明细（含每片对账差异）为 JSON。"""
    return json.dumps(_RUNS[run_id]["run"], ensure_ascii=False, indent=2)


def replay(run_id):
    """按保存的计划和源数据回放一次回填。"""
    ctx = _RUNS[run_id]
    return apply(ctx["plan"], ctx["source_rows"])
