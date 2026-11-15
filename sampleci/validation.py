"""采样计划推广前的单指标全量对比。

流程：先在一个指标上跑全量统计（:func:`full_scan`），再用
:func:`compare` 核对采样估计的误差区间是否覆盖全量真值、偏差是否
在可接受范围内。样本量不足导致没有区间时，:func:`compare` 明确判
``rejected`` 并写明原因，而不是默认通过——避免把不可信的估计推广。
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

from .sample import _interp_quantile
from .stats import _extract_values


def full_scan(rows: Sequence[dict], value_field: str = "value",
              quantiles: Sequence[float] = (0.5, 0.9, 0.95, 0.99)) -> dict:
    """对全量数据做精确统计（普查口径，无抽样误差）。"""
    values = _extract_values(rows, value_field)
    if not values:
        return {
            "n": 0, "mean": None, "variance": None,
            "quantiles": {}, "status": "empty",
        }
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
    ordered = sorted(values)
    qs = {str(p): _interp_quantile(ordered, float(p)) for p in quantiles}
    return {
        "n": n,
        "mean": mean,
        "variance": var,
        "stddev": math.sqrt(var),
        "quantiles": qs,
        "status": "exact",
    }


def compare(
    sampling_result: dict,
    full_result: dict,
    *,
    max_relative_bias: float = 0.05,
    quantile_gate: bool = False,
    value_field: Optional[str] = None,
) -> dict:
    """把采样统计结果与全量真值逐项对比。

    判定规则（均值与分位数分开，均值才是推广的硬口径）：
    - 均值：必须有可信置信区间、区间覆盖全量真值，且相对偏差不超过
      ``max_relative_bias``，任一不满足即拒绝；样本量不足导致没有区间时
      直接判 ``rejected`` 并写明原因，绝不默认通过；
    - 分位数：默认只做提示性检查（``quantile_gate=False``），偏差与区间
      覆盖情况逐点列出但不阻断验收——分位（尤其 P99）对长尾天然敏感；
      显式传 ``quantile_gate=True`` 时同样作为硬门槛。
    """
    from .stats import summarize

    stats = summarize(sampling_result, value_field=value_field)

    checks = []
    true_mean = full_result.get("mean")
    est_mean = stats.get("mean")
    ci = stats.get("ci") or stats.get("range")

    mean_check = {"metric": "mean", "true": true_mean,
                  "estimate": est_mean, "ci": ci, "pass": False,
                  "blocking": True}
    if true_mean is None or est_mean is None:
        mean_check["reason"] = "缺少全量或采样均值"
    elif ci is None:
        mean_check["reason"] = (
            f"样本量不足（status={stats.get('status')}，"
            f"{stats.get('reason')}），无可信误差区间，拒绝推广"
        )
    else:
        covered = ci[0] <= true_mean <= ci[1]
        rel_bias = abs(est_mean - true_mean) / abs(true_mean) \
            if true_mean != 0 else abs(est_mean - true_mean)
        mean_check["relative_bias"] = rel_bias
        mean_check["ci_covers_true"] = covered
        mean_check["pass"] = covered and rel_bias <= max_relative_bias
        if not covered:
            mean_check["reason"] = "置信区间未覆盖全量真值"
        elif rel_bias > max_relative_bias:
            mean_check["reason"] = (
                f"相对偏差 {rel_bias:.4f} 超过阈值 {max_relative_bias}"
            )
    checks.append(mean_check)

    # 分位数逐点对比（分位数区间是 bootstrap 口径，单独标注）
    true_qs = full_result.get("quantiles", {})
    for key, item in (stats.get("quantiles") or {}).items():
        if key not in true_qs:
            continue
        true_q = true_qs[key]
        est_q = item["estimate"]
        q_ci = item.get("ci")
        check = {
            "metric": f"quantile@{key}", "true": true_q,
            "estimate": est_q, "ci": q_ci, "pass": False,
        }
        if true_q == 0:
            rel_bias = abs(est_q)
        else:
            rel_bias = abs(est_q - true_q) / abs(true_q)
        check["relative_bias"] = rel_bias
        check["blocking"] = quantile_gate
        if q_ci is None:
            check["reason"] = item.get("reason", "分位数无区间，仅核对偏差")
            check["pass"] = (rel_bias <= max_relative_bias) or not quantile_gate
        else:
            check["ci_covers_true"] = q_ci[0] <= true_q <= q_ci[1]
            check["pass"] = (
                (check["ci_covers_true"] and rel_bias <= max_relative_bias)
                or not quantile_gate
            )
            if not check["ci_covers_true"]:
                check["reason"] = "bootstrap 区间未覆盖全量真值"
        if rel_bias > max_relative_bias and check["pass"]:
            check["pass"] = False if quantile_gate else True
            check["reason"] = (
                check.get("reason", "")
                or f"相对偏差 {rel_bias:.4f} 超过阈值（提示项，不阻断验收）"
            )
        checks.append(check)

    blocking_checks = [c for c in checks if c.get("blocking", True)]
    accepted = all(c["pass"] for c in blocking_checks) and any(
        c["metric"] == "mean" and c.get("ci") for c in checks
    )
    return {
        "accepted": accepted,
        "verdict": "accepted" if accepted else "rejected",
        "max_relative_bias": max_relative_bias,
        "quantile_gate": quantile_gate,
        "checks": checks,
        "tail": stats.get("tail"),
    }
