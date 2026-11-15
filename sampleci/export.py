"""采样结果与统计报告导出（JSON / CSV），全部 UTF-8、纯标准库。"""
from __future__ import annotations

import csv
import io
import json
from typing import Optional, Sequence


def to_json(sample_data: dict, stats: Optional[dict] = None,
            validation: Optional[dict] = None) -> str:
    """导出完整报告：计划、分层、样本索引、统计口径与全量对比结论。"""
    payload = {
        "plan": sample_data.get("plan"),
        "seed": sample_data.get("seed"),
        "n_total": sample_data.get("n_total"),
        "n_sampled": sample_data.get("n_sampled"),
        "tail_labels": sample_data.get("tail_labels", []),
        "strata": sample_data.get("strata", {}),
        "indices": sample_data.get("indices", []),
        "stats": stats,
        "validation": validation,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)


def samples_to_csv(sample_data: dict, value_field: str = "value") -> str:
    """导出样本明细：全局索引、所属层、层扩张权重、原始行字段。"""
    strata = sample_data.get("strata", {})
    index_to_stratum = {}
    index_to_weight = {}
    for label, meta in strata.items():
        weight = meta["size"] / meta["sampled"] if meta["sampled"] else 0.0
        for idx in meta["indices"]:
            index_to_stratum[idx] = label
            index_to_weight[idx] = weight

    buf = io.StringIO()
    base_fields = ["index", "stratum", "weight", value_field]
    extra = sorted({
        key
        for row in sample_data.get("rows", [])
        if isinstance(row, dict)
        for key in row.keys()
        if key not in base_fields
    })
    fieldnames = base_fields + extra
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for pos, idx in enumerate(sample_data.get("indices", [])):
        row = sample_data["rows"][pos]
        record = {
            "index": idx,
            "stratum": index_to_stratum.get(idx, ""),
            "weight": index_to_weight.get(idx, 1.0),
            value_field: row.get(value_field) if isinstance(row, dict) else "",
        }
        if isinstance(row, dict):
            for key in extra:
                record[key] = row.get(key, "")
        writer.writerow(record)
    return buf.getvalue()


def report_to_csv(stats: dict) -> str:
    """把统计报告导成长表：指标 / 估计 / 区间下界 / 上界 / 状态。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric", "estimate", "ci_low", "ci_high",
                     "confidence", "status", "reason"])
    ci = stats.get("ci")
    writer.writerow([
        "mean", stats.get("mean"),
        ci[0] if ci else "", ci[1] if ci else "",
        stats.get("ci_confidence", ""), stats.get("status", ""),
        stats.get("reason", ""),
    ])
    for key, item in (stats.get("quantiles") or {}).items():
        q_ci = item.get("ci")
        writer.writerow([
            f"quantile@{key}", item.get("estimate"),
            q_ci[0] if q_ci else "", q_ci[1] if q_ci else "",
            stats.get("ci_confidence", ""),
            "ok" if q_ci else "point_estimate_only",
            item.get("reason", ""),
        ])
    tail = (stats.get("tail") or {}).get("summary") or {}
    if tail and tail.get("n_sampled"):
        t_ci = tail.get("ci")
        writer.writerow([
            "tail_mean", tail.get("mean"),
            t_ci[0] if t_ci else "", t_ci[1] if t_ci else "",
            tail.get("ci_confidence", ""), tail.get("status", ""),
            tail.get("reason", ""),
        ])
    return buf.getvalue()


def write_files(path_base: str, sample_data: dict,
                stats: Optional[dict] = None,
                validation: Optional[dict] = None,
                value_field: str = "value") -> Sequence[str]:
    """一次性写出 .json / 样本明细 .csv / 统计报告 .csv，返回文件路径。"""
    paths = [f"{path_base}.json", f"{path_base}.samples.csv",
             f"{path_base}.report.csv"]
    with open(paths[0], "w", encoding="utf-8") as fh:
        fh.write(to_json(sample_data, stats, validation))
    with open(paths[1], "w", encoding="utf-8", newline="") as fh:
        fh.write(samples_to_csv(sample_data, value_field))
    if stats is not None:
        with open(paths[2], "w", encoding="utf-8", newline="") as fh:
            fh.write(report_to_csv(stats))
    else:
        paths.pop()
    return paths
