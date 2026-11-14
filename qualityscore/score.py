"""加权质量评分、按天趋势与告警，纯标准库。"""

import json
from datetime import date

DEFAULT_CONFIG = {
    "threshold": 0.8,       # 总分低于该阈值告警
    "decline_days": 2,      # 连续下降达到该天数才告警
    "window": 7,            # 趋势窗口（天）
    "history_file": None,   # 留档路径（JSON），None 表示不留档
}

_config = dict(DEFAULT_CONFIG)
_history = {}  # dataset -> [{"date", "score", "breakdown"}]，按日期升序，一天一条


def configure(**kwargs):
    """更新配置并返回当前配置；设置 history_file 时会加载已有留档。"""
    unknown = sorted(set(kwargs) - set(DEFAULT_CONFIG))
    if unknown:
        raise ValueError("unknown config: %s" % ", ".join(unknown))
    _config.update(kwargs)
    if _config.get("history_file"):
        _load(_config["history_file"])
    return dict(_config)


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    for dataset, entries in data.items():
        if isinstance(entries, list):
            _history[dataset] = entries


def _save():
    path = _config.get("history_file")
    if not path:
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_history, fh, ensure_ascii=False, indent=2, sort_keys=True)


def _rule_view(rule):
    """归一化一条规则：缺省 weight=1、level=normal，failed 布尔折算为 0/1 行。"""
    failed_rows = rule.get("failed_rows")
    total_rows = rule.get("total_rows")
    if failed_rows is None and total_rows is None:
        failed_rows = 1 if rule.get("failed") else 0
        total_rows = 1
    failed_rows = failed_rows or 0
    total_rows = total_rows or 0
    value = 1.0 if total_rows <= 0 else max(0.0, 1.0 - failed_rows / total_rows)
    return {
        "name": rule.get("name") or rule.get("rule") or "rule",
        "level": rule.get("level", "normal"),
        "weight": rule.get("weight", 1),
        "failed_rows": failed_rows,
        "total_rows": total_rows,
        "score": round(value, 4),
    }


def score(batch):
    """加权评分：sum(weight * 规则分) / sum(weight)，规则分按失败行数折算。"""
    views = [_rule_view(rule) for rule in batch.get("rules") or []]
    failed = sum(1 for v in views if v["failed_rows"] > 0)
    total = max(1, len(views))
    weight_sum = sum(v["weight"] for v in views) or 1
    value = round(sum(v["weight"] * v["score"] for v in views) / weight_sum, 2)

    day = batch.get("date") or date.today().isoformat()
    dataset = batch.get("dataset", "default")
    entries = _history.setdefault(dataset, [])
    entries[:] = [e for e in entries if e["date"] != day]
    entries.append({"date": day, "score": value, "breakdown": views})
    entries.sort(key=lambda e: e["date"])
    _save()

    report = {
        "score": value,
        "failed": failed,
        "total": total,
        "date": day,
        "breakdown": views,
        "trend": _trend_summary(dataset),
        "alerts": _alerts(dataset),
    }
    drivers = _decline_drivers(dataset)
    if drivers:
        report["decline_drivers"] = drivers
    return report


def trend(dataset, window=None, detail=False):
    """最近 window 天的总分列表；detail=True 时带日期、环比和分项，可回看。"""
    entries = _window_entries(dataset, window)
    if not detail:
        return [e["score"] for e in entries]
    result = []
    prev = None
    for entry in entries:
        item = dict(entry)
        item["delta"] = round(entry["score"] - prev, 2) if prev is not None else 0.0
        result.append(item)
        prev = entry["score"]
    return result


def _entries(dataset):
    return _history.get(dataset) or []


def _window_entries(dataset, window=None):
    entries = _entries(dataset)
    n = window if window is not None else _config["window"]
    return entries[-n:] if n else list(entries)


def _trend_summary(dataset):
    entries = _window_entries(dataset)
    scores = [e["score"] for e in entries]
    delta = round(scores[-1] - scores[-2], 2) if len(scores) >= 2 else 0.0
    decline = 0
    for prev, cur in zip(scores, scores[1:]):
        decline = decline + 1 if cur < prev else 0
    direction = "flat"
    if len(scores) >= 2:
        if scores[-1] > scores[0]:
            direction = "up"
        elif scores[-1] < scores[0]:
            direction = "down"
    return {
        "window": len(scores),
        "scores": scores,
        "delta": delta,
        "direction": direction,
        "consecutive_decline": decline,
    }


def _alerts(dataset):
    entries = _window_entries(dataset)
    if not entries:
        return []
    alerts = []
    latest = entries[-1]
    threshold = _config["threshold"]
    if latest["score"] < threshold:
        alerts.append({
            "type": "below_threshold",
            "date": latest["date"],
            "message": "总分 %.2f 低于阈值 %.2f" % (latest["score"], threshold),
        })
    decline = _trend_summary(dataset)["consecutive_decline"]
    if decline >= _config["decline_days"]:
        alerts.append({
            "type": "consecutive_decline",
            "date": latest["date"],
            "message": "总分连续 %d 天下降" % decline,
        })
    return alerts


def _decline_drivers(dataset):
    """总分环比下降时，按加权影响列出拖后腿的规则（下钻）。"""
    entries = _entries(dataset)
    if len(entries) < 2:
        return []
    prev, cur = entries[-2], entries[-1]
    if cur["score"] >= prev["score"]:
        return []
    prev_map = {v["name"]: v for v in prev["breakdown"]}
    drivers = []
    for view in cur["breakdown"]:
        old = prev_map.get(view["name"])
        old_score = old["score"] if old else 1.0
        drop = old_score - view["score"]
        if drop > 0:
            drivers.append({
                "rule": view["name"],
                "level": view["level"],
                "weight": view["weight"],
                "from": old_score,
                "to": view["score"],
                "impact": round(drop * view["weight"], 4),
            })
    drivers.sort(key=lambda d: d["impact"], reverse=True)
    return drivers
