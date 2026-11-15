"""采样结果的统计与误差区间。

输出口径（均值与分位数分开说明，样本量不足时不给误导性区间）：

- ``mean``：总体均值的分层无偏估计，普查层按层权重精确计入；
- ``ci``：均值的分层 t 置信区间（默认 95%）。任一非普查层样本少于
  ``min_n_per_stratum``（默认 2）或总样本少于 ``min_n``（默认 2）时，
  ``range=None`` 且在 ``status`` / ``reason`` 中说明原因；
- ``quantiles``：加权分位数点估计（权重为层扩张因子 N_h/n_h，普查层
  权重为 1），与均值分开列出；其区间用分层 bootstrap（计划种子驱动，
  可复现），样本不足或关闭时只给点估计；
- ``tail``：长尾层单独汇总，与主体层分开，不混进主区间口径；
- 普查（census）层方差贡献为 0，区间只反映抽样层的不确定性。
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Sequence

from .sample import _derived_seed, _interp_quantile

DEFAULT_QUANTILES = (0.5, 0.9, 0.95, 0.99)


def _extract_values(rows: Sequence[dict], value_field: str) -> List[float]:
    values = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        v = row.get(value_field)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        values.append(float(v))
    return values


def _sample_variance(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return sum((v - mean) ** 2 for v in values) / (n - 1)


def _t_critical(df: float, alpha: float = 0.05) -> float:
    """t 分布双侧临界值，反算正则化不完全贝塔函数（纯标准库）。"""
    if df <= 0:
        return float("nan")
    p_target = 1.0 - alpha / 2.0

    def cdf(t: float) -> float:
        x = df / (df + t * t)
        # P(T <= t), t>0 => 1 - 0.5 * I_x(df/2, 1/2)
        return 1.0 - 0.5 * _betai(df / 2.0, 0.5, x)

    lo, hi = 0.0, 1.0
    while cdf(hi) < p_target:
        hi *= 2.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if cdf(mid) < p_target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _betacf(a: float, b: float, x: float) -> float:
    """连分式展开（Numerical Recipes betacf）。"""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """正则化不完全贝塔函数 I_x(a, b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x)) / a
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x)
    return 1.0 - (
        math.exp(lbeta + a * math.log(x) + b * math.log1p(-x)) / b
    ) * _betacf(b, a, 1.0 - x)


def _weighted_quantile(
    pairs: Sequence[tuple], probs: Sequence[float]
) -> Dict[str, float]:
    """加权分位数（R-7 风格的累计权重线性插值，与全量分位数同口径）。

    pairs: (value, weight)，权重为层扩张因子 N_h/n_h（普查层为 1）。
    把权重记为 w_k、累计权重 S_k=sum_{j<=k} w_j。样本点 k 的 0 基网格
    坐标为 x_k = S_k - w_k（等权时 x_k=k，即标准 R-7 的 h=p*(n-1)）；
    在相邻点坐标 x_k 之间对 h=p*(S_n-1) 线性插值。
    """
    ordered = sorted(pairs, key=lambda item: item[0])
    total_w = sum(w for _, w in ordered)
    out: Dict[str, float] = {}
    if total_w <= 0:
        return {str(p): float("nan") for p in probs}
    grid = []          # 每个点的 0 基网格坐标
    s = 0.0
    for _, w in ordered:
        grid.append(s)
        s += w
    for p in probs:
        p = float(p)
        h = p * (total_w - 1.0)
        if h <= 0:
            out[str(p)] = ordered[0][0]
            continue
        if h >= total_w - 1.0:
            out[str(p)] = ordered[-1][0]
            continue
        # 找到第一个网格坐标严格大于 h 的点 k，在点 k-1 与点 k 之间插值
        k = 1
        while k < len(grid) and grid[k] <= h:
            k += 1
        k = min(k, len(grid) - 1)
        x_lo, x_hi = grid[k - 1], grid[k]
        v_lo, v_hi = ordered[k - 1][0], ordered[k][0]
        frac = (h - x_lo) / (x_hi - x_lo) if x_hi > x_lo else 0.0
        out[str(p)] = v_lo + frac * (v_hi - v_lo)
    return out


def _bootstrap_quantile_ci(
    strata_values: Dict[str, List[float]],
    strata_weights: Dict[str, float],
    rng: random.Random,
    probs: Sequence[float],
    n_boot: int,
    confidence: float,
) -> Dict[str, List[float]]:
    """分层 bootstrap：每层在本层样本内有放回重抽 n_h 次，逐次算加权分位。"""
    labels = [label for label, vals in strata_values.items() if vals]
    if not labels:
        return {}
    boot_stats: Dict[str, List[float]] = {str(p): [] for p in probs}
    for _ in range(n_boot):
        pairs = []
        for label in labels:
            vals = strata_values[label]
            w = strata_weights[label]
            draws = (vals[rng.randrange(len(vals))] for _ in vals)
            pairs.extend((v, w) for v in draws)
        qs = _weighted_quantile(pairs, probs)
        for p in probs:
            boot_stats[str(p)].append(qs[str(p)])
    alpha = 1.0 - confidence
    lo_p, hi_p = alpha / 2.0, 1.0 - alpha / 2.0
    return {
        p: [_interp_quantile(sorted(stats), lo_p),
            _interp_quantile(sorted(stats), hi_p)]
        for p, stats in boot_stats.items()
    }


def _summarize_group(
    strata_rows: Dict[str, List[dict]],
    strata_meta: Dict[str, dict],
    plan: dict,
    probs: Sequence[float],
    confidence: float,
    min_n: int,
    min_n_per_stratum: int,
    bootstrap: bool,
    n_boot: int,
) -> dict:
    """对一组层（主体或长尾）做完整统计。"""
    value_field = plan["value_field"]
    total_pop_raw = sum(meta["size"] for meta in strata_meta.values())
    sampled_n_raw = sum(len(rows) for rows in strata_rows.values())

    if total_pop_raw == 0 or sampled_n_raw == 0:
        return {
            "n_population": total_pop_raw,
            "n_sampled": sampled_n_raw,
            "mean": None,
            "ci": None,
            "quantiles": {},
            "status": "empty",
            "reason": "该组没有可统计的数据",
        }

    # 每层：均值、方差、扩张权重 N_h/n_h（普查层为 1）
    per_stratum = {}
    weighted_mean_num = 0.0
    variance_num = 0.0
    df_num = 0.0
    df_den = 0.0
    pairs: List[tuple] = []
    bootstrap_values: Dict[str, List[float]] = {}
    bootstrap_weights: Dict[str, float] = {}
    weak_strata = []
    effective_pop = 0
    sampled_n = 0

    for label, rows in strata_rows.items():
        meta = strata_meta[label]
        N_h = meta["size"]
        vals = _extract_values(rows, value_field)
        if not vals:
            # 整层都没有可用数值：不参与估计，层大小从口径中剔除，
            # 避免脏行稀释均值
            continue
        effective_pop += N_h
        n_h = len(vals)
        sampled_n += n_h
        mean_h = sum(vals) / n_h
        var_h = _sample_variance(vals)
        is_census = meta.get("mode") == "census" or n_h == N_h
        # 扩张权重：抽样层 N_h/n_h（把样本还原到层规模）；
        # 普查层每个点就是它自己，权重恒为 1，不能再放大
        weight_h = 1.0 if is_census else N_h / n_h
        per_stratum[label] = {
            "size": N_h,
            "sampled": n_h,
            "sampled_rows": meta.get("sampled", len(rows)),
            "mean": mean_h,
            "variance": var_h,
            "weight": weight_h,
            "census": is_census,
        }
        weighted_mean_num += N_h * mean_h
        if not is_census:
            # 分层方差贡献：(N_h^2) * (1 - n_h/N_h) * s_h^2 / n_h
            contribution = (N_h ** 2) * (1.0 - n_h / N_h) * var_h / n_h
            variance_num += contribution
            term = contribution / N_h if N_h else 0.0
            df_num += term ** 2
            if n_h > 1:
                df_den += term ** 2 / (n_h - 1)
            if n_h < min_n_per_stratum:
                weak_strata.append(label)
        pairs.extend((v, weight_h) for v in vals)
        bootstrap_values[label] = vals
        bootstrap_weights[label] = weight_h

    if effective_pop == 0 or sampled_n == 0:
        return {
            "n_population": total_pop_raw,
            "n_sampled": sampled_n_raw,
            "mean": None,
            "ci": None,
            "quantiles": {},
            "status": "empty",
            "reason": "没有可统计的数值字段",
        }

    mean = weighted_mean_num / effective_pop
    var_mean = variance_num / (effective_pop ** 2)

    # ---- 均值置信区间（Welch–Satterthwaite 自由度的分层 t 区间）----
    ci = None
    status = "ok"
    reason = None
    if sampled_n < min_n:
        status = "insufficient"
        reason = f"总样本量 {sampled_n} < min_n {min_n}，不给出误差区间"
    elif weak_strata:
        status = "insufficient"
        reason = (
            f"非普查层 {weak_strata} 样本量 < min_n_per_stratum="
            f"{min_n_per_stratum}，方差估计不可靠，不给出误差区间"
        )
    elif var_mean <= 0.0:
        # 全部普查（或抽样层方差为 0）：均值对该总体是精确的
        ci = [mean, mean]
        status = "exact" if all(
            m["census"] for m in per_stratum.values()
        ) else "zero_variance"
    else:
        se = math.sqrt(var_mean)
        df = (df_num ** 2) / df_den if df_den > 0 else float("inf")
        t = _t_critical(df, alpha=1.0 - confidence) if math.isfinite(df) \
            else _normal_critical(1.0 - confidence)
        ci = [mean - t * se, mean + t * se]

    # ---- 分位数（点估计 + 可选 bootstrap 区间），与均值分开说明 ----
    # 两个口径并列：
    #   estimate_weighted：总体加权分位，用层扩张权重把样本还原到总体规模；
    #     分层抽样下高抽样率层（如普查长尾层）权重为 1，与抽样率低的主体层
    #     混合时，尾部极端分位可能与全量真值有系统性差距——如实标注；
    #   estimate_sample：等权样本分位，描述“抽到的样本”本身，不做总体外推。
    quantiles = _weighted_quantile(pairs, probs)
    sample_quantiles = _weighted_quantile(
        [(v, 1.0) for v, _ in pairs], probs
    )
    quantile_note = (
        "estimate_weighted 为总体加权分位（层扩张权重，普查层权重=1）；"
        "estimate_sample 为等权样本分位，不外推总体。极端分位在不等比"
        "分层抽样下可能偏离全量真值，均值区间才是主要误差口径。"
    )
    quantile_out: Dict[str, dict] = {}
    if bootstrap and sampled_n >= min_n and not weak_strata:
        rng = random.Random(_derived_seed(plan["seed"], "quantile-bootstrap"))
        bands = _bootstrap_quantile_ci(
            bootstrap_values, bootstrap_weights, rng,
            probs, n_boot, confidence,
        )
        for p in probs:
            key = str(p)
            quantile_out[key] = {
                "estimate": quantiles[key],
                "estimate_weighted": quantiles[key],
                "estimate_sample": sample_quantiles[key],
                "ci": bands[key],
                "note": quantile_note,
            }
    else:
        for p in probs:
            key = str(p)
            quantile_out[key] = {
                "estimate": quantiles[key],
                "estimate_weighted": quantiles[key],
                "estimate_sample": sample_quantiles[key],
                "ci": None,
                "reason": reason or "bootstrap 已关闭，仅给出点估计",
                "note": quantile_note,
            }

    return {
        "n_population": effective_pop,
        "n_population_rows": total_pop_raw,
        "n_sampled": sampled_n,
        "n_sampled_rows": sampled_n_raw,
        "mean": mean,
        "ci": ci,
        "ci_confidence": confidence,
        "quantiles": quantile_out,
        "per_stratum": per_stratum,
        "status": status,
        "reason": reason,
    }


def _normal_critical(confidence: float) -> float:
    """标准正态双侧临界值（Acklam 逆 CDF 近似）。"""
    p = 1.0 - (1.0 - confidence) / 2.0

    # 用二分 + erf 实现，避免近似系数误差
    lo, hi = 0.0, 1.0
    while 0.5 * (1.0 + math.erf(hi / math.sqrt(2.0))) < p:
        hi *= 2.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if 0.5 * (1.0 + math.erf(mid / math.sqrt(2.0))) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def summarize(
    sample_data: dict,
    *,
    value_field: Optional[str] = None,
    quantiles: Sequence[float] = DEFAULT_QUANTILES,
    confidence: float = 0.95,
    min_n: int = 2,
    min_n_per_stratum: int = 2,
    bootstrap: bool = True,
    n_boot: int = 1000,
) -> dict:
    """计算采样结果的均值、分位数与误差区间。

    参数可通过 ``sample_data["plan"]`` 中的同名键覆盖默认值
    （``min_n`` / ``min_n_per_stratum`` / ``bootstrap`` / ``n_boot`` /
    ``confidence`` / ``quantiles``），保证“同一计划同一结果”。
    """
    plan = dict(sample_data.get("plan") or {})
    if value_field is None:
        value_field = plan.get("value_field", "value")
    plan = dict(plan)
    plan.setdefault("seed", 0)
    plan["value_field"] = value_field

    def pick(name, default):
        return plan.get(name, default)

    confidence = float(pick("confidence", confidence))
    min_n = int(pick("min_n", min_n))
    min_n_per_stratum = int(pick("min_n_per_stratum", min_n_per_stratum))
    bootstrap = bool(pick("bootstrap", bootstrap))
    n_boot = int(pick("n_boot", n_boot))
    probs = tuple(pick("quantiles", quantiles))

    rows = sample_data.get("rows", [])
    strata_meta = sample_data.get("strata") or {
        "_all": {
            "size": sample_data.get("n_total", len(rows)),
            "sampled": len(rows),
            "mode": "census"
            if sample_data.get("n_total", len(rows)) == len(rows)
            else "ratio",
            "indices": sample_data.get("indices", list(range(len(rows)))),
            "tail": False,
        }
    }
    tail_labels = set(sample_data.get("tail_labels", []))

    # 按层把样本行归回去（依据 strata 中保存的索引，避免依赖行内字段）
    global_indices = sample_data.get("indices")
    if global_indices is None:
        # 无索引信息时退化为整包单组
        strata_rows = {"_all": list(rows)}
        main_labels, tail_labels = ["_all"], set()
    else:
        index_pos = {idx: pos for pos, idx in enumerate(global_indices)}
        strata_rows = {}
        for label, meta in strata_meta.items():
            strata_rows[label] = [rows[index_pos[i]] for i in meta["indices"]]
        main_labels = [label for label in strata_meta if label not in tail_labels]

    common = dict(
        plan=plan, probs=probs, confidence=confidence, min_n=min_n,
        min_n_per_stratum=min_n_per_stratum, bootstrap=bootstrap,
        n_boot=n_boot,
    )
    all_labels = list(strata_meta)
    # 总体口径：全部层（长尾普查层精确计入，方差贡献为 0）
    overall = _summarize_group(
        {k: strata_rows[k] for k in all_labels if k in strata_rows},
        {k: strata_meta[k] for k in all_labels},
        **common,
    )
    # 分组明细：主体层与长尾层各自单独统计
    main_group = _summarize_group(
        {k: strata_rows[k] for k in main_labels if k in strata_rows},
        {k: strata_meta[k] for k in main_labels},
        **common,
    )
    tail_group = _summarize_group(
        {k: strata_rows[k] for k in tail_labels if k in strata_rows},
        {k: strata_meta[k] for k in tail_labels},
        **common,
    )

    return {
        "value_field": value_field,
        "n_total": sample_data.get("n_total", len(rows)),
        "n_sampled": overall["n_sampled"],
        "n_sampled_rows": overall.get("n_sampled_rows", len(rows)),
        "seed": sample_data.get("seed"),
        # ---- 总体估计（均值与分位数分开；区间不足时为 None）----
        "mean": overall["mean"],
        "quantiles": overall["quantiles"],
        "range": overall["ci"],
        "ci": overall["ci"],
        "ci_confidence": confidence,
        "status": overall["status"],
        "reason": overall["reason"],
        "per_stratum": overall.get("per_stratum", {}),
        # ---- 分组明细：长尾层单独统计，不与主体混为一组 ----
        "main": {
            "labels": sorted(main_labels),
            "summary": main_group,
        },
        "tail": {
            "labels": sorted(tail_labels),
            "summary": tail_group,
        },
    }


def error(sample_data: dict, **kwargs) -> dict:
    """兼容旧接口名；等价于 :func:`summarize`。"""
    return summarize(sample_data, **kwargs)
