"""可复现分层采样与误差估计，纯标准库。

口径：
- 按 plan["stratify_by"] 字段分层；数值字段可用 plan["boundaries"] 配置分层边界。
- 种子：plan["seed"] 显式给定，否则按查询条件（plan 规范化内容）哈希生成，固定可复现。
- 每层按比例 ratio 或固定条数 size 抽样，可按层混用（plan["strata"] 覆盖默认规则）。
- 误差：均值 ± z(置信水平) * 标准差 / sqrt(n)，按字段输出，默认置信水平 95%。
- 长尾层：>= 最大边界的层，或总体占比 < tail_share（默认 5%）的层，单独统计。
"""
import hashlib
import json
import math
import random
import statistics

DEFAULT_RATIO = 0.1
DEFAULT_CONFIDENCE = 0.95
DEFAULT_MIN_SIZE = 30
DEFAULT_TAIL_SHARE = 0.05
DEFAULT_QUANTILES = (50, 90, 99)
STRATUM_TAG = "_stratum"

_SEED_KEYS = ("stratify_by", "ratio", "size", "boundaries", "query", "strata", "tail_share")


def _derive_seed(plan):
    """按查询条件生成固定种子。"""
    payload = {key: plan.get(key) for key in _SEED_KEYS if key in plan}
    text = json.dumps(payload, sort_keys=True, default=str)
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)


def _stratum_name(value, boundaries):
    if boundaries:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "null"
        lower = None
        for bound in boundaries:
            if number < bound:
                return "<%s" % bound if lower is None else "%s-%s" % (lower, bound)
            lower = bound
        return ">=%s" % lower
    return "null" if value is None else str(value)


def _alloc_count(population, rule):
    """按比例或固定条数计算本层抽样条数。"""
    if "size" in rule:
        return min(max(int(rule["size"]), 0), population)
    ratio = float(rule["ratio"])
    if ratio <= 0 or population == 0:
        return 0
    if ratio >= 1:
        return population
    return max(1, int(population * ratio))


def _quantile(sorted_values, q):
    if not sorted_values:
        return None
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    pos = (n - 1) * q
    low = int(pos)
    high = min(low + 1, n - 1)
    frac = pos - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


def _numeric_values(rows, field):
    values = []
    for row in rows:
        if isinstance(row, dict):
            value = row.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
    return values


def _field_stats(values, confidence, min_size, quantiles):
    n = len(values)
    if n == 0:
        return None
    mean = sum(values) / n
    stdev = statistics.stdev(values) if n >= 2 else 0.0
    z = statistics.NormalDist().inv_cdf((1 + confidence) / 2)
    margin = z * stdev / math.sqrt(n)
    ordered = sorted(values)
    warning = None
    if n < min_size:
        warning = "样本量不足：仅 %d 条（建议 >= %d），误差范围仅供参考" % (n, min_size)
    return {
        "n": n,
        "mean": mean,
        "stdev": stdev,
        "quantiles": {"p%s" % q: _quantile(ordered, q / 100.0) for q in quantiles},
        "error": margin,
        "error_range": [mean - margin, mean + margin],
        "confidence": confidence,
        "warning": warning,
    }


def sample(rows, plan):
    """可复现分层采样。返回分层、带标记的样本和可重放的计划。"""
    plan = dict(plan or {})
    rows = [dict(row) if isinstance(row, dict) else row for row in rows]
    stratify_by = plan.get("stratify_by")
    boundaries = sorted(plan["boundaries"]) if plan.get("boundaries") else None
    seed = plan.get("seed")
    if seed is None:
        seed = _derive_seed(plan)
    default_rule = {"size": plan["size"]} if plan.get("size") is not None else None
    if default_rule is None:
        default_rule = {"ratio": float(plan.get("ratio", DEFAULT_RATIO))}
    overrides = plan.get("strata", {})
    min_size = int(plan.get("min_size", DEFAULT_MIN_SIZE))

    groups = {}
    for row in rows:
        if stratify_by and isinstance(row, dict):
            name = _stratum_name(row.get(stratify_by), boundaries)
        else:
            name = "all"
        groups.setdefault(name, []).append(row)

    total = len(rows)
    tail_share = float(plan.get("tail_share", DEFAULT_TAIL_SHARE))
    tail_names = set()
    for name, members in groups.items():
        if boundaries:
            if name == ">=%s" % boundaries[-1]:
                tail_names.add(name)
        elif total and len(members) / total < tail_share:
            tail_names.add(name)

    picked = []
    strata_info = {}
    for name in sorted(groups):
        members = groups[name]
        population = len(members)
        rule = dict(overrides.get(name) or default_rule)
        count = _alloc_count(population, rule)
        rng = random.Random("%s|%s" % (seed, name))
        chosen = list(members) if count >= population else rng.sample(members, count)
        warning = None
        if 0 < count < min_size:
            warning = "样本量不足：层 %s 仅 %d 条（建议 >= %d）" % (name, count, min_size)
        for row in chosen:
            if isinstance(row, dict):
                row[STRATUM_TAG] = name
        picked.extend(chosen)
        strata_info[name] = {
            "population": population,
            "sampled": count,
            "rule": rule,
            "long_tail": name in tail_names,
            "warning": warning,
        }

    resolved = dict(plan)
    resolved["seed"] = seed
    return {
        "rows": picked,
        "strata": strata_info,
        "seed": None,  # 兼容旧接口；实际种子见 plan["seed"]
        "plan": resolved,
        "confidence": float(plan.get("confidence", DEFAULT_CONFIDENCE)),
    }


def error(sample_data):
    """按字段输出均值、分位数和误差范围，长尾层单独统计。"""
    rows = list(sample_data.get("rows", []))
    plan = sample_data.get("plan", {})
    strata_info = sample_data.get("strata", {})
    confidence = float(sample_data.get("confidence", plan.get("confidence", DEFAULT_CONFIDENCE)))
    min_size = int(plan.get("min_size", DEFAULT_MIN_SIZE))
    quantiles = tuple(plan.get("quantiles", DEFAULT_QUANTILES))

    fields = sorted({
        key
        for row in rows if isinstance(row, dict)
        for key, value in row.items()
        if key != STRATUM_TAG and isinstance(value, (int, float)) and not isinstance(value, bool)
    })
    field_report = {}
    for field in fields:
        stats = _field_stats(_numeric_values(rows, field), confidence, min_size, quantiles)
        if stats:
            field_report[field] = stats

    by_stratum = {}
    for row in rows:
        if isinstance(row, dict) and STRATUM_TAG in row:
            by_stratum.setdefault(row[STRATUM_TAG], []).append(row)
    strata_report = {}
    long_tail = {}
    for name in sorted(by_stratum):
        members = by_stratum[name]
        stats = {}
        for field in fields:
            field_stat = _field_stats(_numeric_values(members, field), confidence, min_size, quantiles)
            if field_stat:
                stats[field] = field_stat
        entry = {"n": len(members), "fields": stats}
        strata_report[name] = entry
        if strata_info.get(name, {}).get("long_tail"):
            long_tail[name] = entry

    values = _numeric_values(rows, "value")
    mean = sum(values) / len(values) if values else 0.0
    legacy_quantiles = field_report.get("value", {}).get("quantiles", {})
    warnings = [info["warning"] for info in strata_info.values() if info.get("warning")]
    return {
        "mean": mean,
        "quantiles": legacy_quantiles,
        "range": None,  # 兼容旧接口；误差范围见 fields[...]["error_range"]
        "confidence": confidence,
        "fields": field_report,
        "strata": strata_report,
        "long_tail": long_tail,
        "warnings": warnings,
    }
