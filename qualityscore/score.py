"""加权质量评分、趋势环比与告警。

- 规则带 weight（默认 1.0）和 level（默认 info），按失败行数折算单规则分。
- 按天保留总分与分项，支持回看与下钻。
- 阈值、连续下降天数、趋势窗口均可通过 configure() 配置。
"""

from datetime import date as _date

# {dataset: [{"date": str, "score": float, "breakdown": [...]}]}，按日期升序，同日覆盖
_history = {}

_config = {
    "threshold": 0.8,   # 总分低于该阈值触发 below_threshold 告警
    "decline_days": 3,  # 连续下降达到该天数触发 consecutive_decline 告警
    "window": 7,        # 趋势计算的最近窗口（天）
}

_CONFIG_DEFAULTS = dict(_config)


def configure(**kwargs):
    """更新阈值/连续下降天数/趋势窗口等配置，返回当前配置。"""
    for key, value in kwargs.items():
        if key not in _config:
            raise KeyError("unknown config key: %s" % key)
        _config[key] = value
    return dict(_config)


def reset_config():
    """恢复默认配置。"""
    _config.clear()
    _config.update(_CONFIG_DEFAULTS)
    return dict(_config)


def _rule_score(rule, batch_rows):
    """单规则得分与失败行数。failed 为布尔时兼容旧口径（失败即 0 分）。"""
    failed = rule.get("failed", 0)
    if isinstance(failed, bool):
        return (0.0 if failed else 1.0), (1 if failed else 0)
    rows = rule.get("rows", batch_rows)
    if rows:
        return max(0.0, 1.0 - failed / rows), failed
    return (0.0 if failed else 1.0), failed


def _record(dataset, day, value, breakdown):
    entries = _history.setdefault(dataset, [])
    for i, entry in enumerate(entries):
        if entry["date"] == day:
            entries[i] = {"date": day, "score": value, "breakdown": breakdown}
            return entries
    entries.append({"date": day, "score": value, "breakdown": breakdown})
    entries.sort(key=lambda e: e["date"])
    return entries


def _trend_info(entries, breakdown):
    window = _config["window"]
    recent = entries[-window:]
    scores = [e["score"] for e in recent]
    delta = round(scores[-1] - scores[-2], 2) if len(scores) > 1 else 0.0
    declines = 0
    for prev, cur in zip(reversed(scores), reversed(scores[:-1])):
        if prev < cur:
            declines += 1
        else:
            break
    # 下钻：与前一天相比，单规则得分变化（按规则名对齐）
    drivers = []
    if len(entries) > 1:
        prev_by_name = {r["name"]: r["score"] for r in entries[-2]["breakdown"]}
        for item in breakdown:
            prev = prev_by_name.get(item["name"])
            if prev is not None:
                change = round(item["score"] - prev, 4)
                if change < 0:
                    drivers.append({"name": item["name"], "level": item["level"],
                                    "delta": change})
        drivers.sort(key=lambda d: d["delta"])
    return {
        "window": window,
        "scores": scores,
        "delta": delta,
        "direction": "down" if delta < 0 else ("up" if delta > 0 else "flat"),
        "consecutive_declines": declines,
        "drivers": drivers,
    }


def _alerts(value, trend_info):
    alerts = []
    if value < _config["threshold"]:
        alerts.append({
            "type": "below_threshold",
            "message": "总分 %.2f 低于阈值 %.2f" % (value, _config["threshold"]),
        })
    days = trend_info["consecutive_declines"]
    if days >= _config["decline_days"]:
        alerts.append({
            "type": "consecutive_decline",
            "days": days,
            "message": "总分已连续 %d 天下降" % days,
        })
    return alerts


def score(batch):
    """加权评分：总分 = Σ(weight × 规则分) / Σ(weight)，规则分按失败行数折算。"""
    dataset = batch.get("dataset", "default")
    day = batch.get("date") or _date.today().isoformat()
    batch_rows = batch.get("rows")
    rules = batch.get("rules") or []

    breakdown = []
    total_weight = 0.0
    weighted = 0.0
    failed_rules = 0
    for i, rule in enumerate(rules):
        weight = float(rule.get("weight", 1.0))
        rscore, failed = _rule_score(rule, batch_rows)
        if failed:
            failed_rules += 1
        total_weight += weight
        weighted += weight * rscore
        breakdown.append({
            "name": rule.get("name", "rule_%d" % i),
            "level": rule.get("level", "info"),
            "weight": weight,
            "failed": failed,
            "score": round(rscore, 4),
        })
    value = round(weighted / total_weight, 2) if total_weight else 1.0
    for item in breakdown:
        item["contribution"] = round(item["weight"] * item["score"] / total_weight, 4) \
            if total_weight else 0.0

    entries = _record(dataset, day, value, breakdown)
    trend_info = _trend_info(entries, breakdown)
    return {
        "dataset": dataset,
        "date": day,
        "score": value,
        "failed": failed_rules,
        "total": len(rules),
        "breakdown": breakdown,
        "trend": trend_info,
        "alerts": _alerts(value, trend_info),
    }


def trend(dataset, days=None):
    """按天返回历史总分（升序）；days 限定回看最近 N 天。"""
    entries = _history.get(dataset) or []
    if days is not None:
        entries = entries[-days:]
    return [e["score"] for e in entries]


def report(dataset):
    """完整留档：每日总分与分项、趋势、告警、当前配置，可序列化保存。"""
    entries = list(_history.get(dataset) or [])
    latest = entries[-1] if entries else None
    trend_info = _trend_info(entries, latest["breakdown"]) if latest else None
    return {
        "dataset": dataset,
        "config": dict(_config),
        "days": entries,
        "trend": trend_info,
        "alerts": _alerts(latest["score"], trend_info) if latest else [],
    }
