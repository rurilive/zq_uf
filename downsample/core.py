"""按时间窗口聚合的降采样。

rule 为整数时保持旧的等间隔抽点行为；为字符串或 dict 时按窗口聚合。
"""

import re
from datetime import datetime, timedelta, timezone

__all__ = ["resample", "rollup"]

_AGGS = ("min", "max", "mean", "last")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FIXED_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_CALENDAR_UNITS = ("day", "week", "month")
_RULE_RE = re.compile(r"(\d+)\s*(month|week|day|[smhd])")


def resample(points, rule, aggs=None):
    """把 points（{"ts": epoch秒, "v": 值}）按 rule 降采样。

    rule 为整数：旧的等间隔抽点。rule 为 "5m"/"1day" 等字符串或
    {"every": ..., "fill": ..., "duplicate": ..., "origin": ...} 时按窗口聚合。
    """
    if isinstance(rule, int):
        step = int(rule)
        return [point for index, point in enumerate(points) if index % step == 0]
    spec = _parse_rule(rule)
    contribs = _point_contribs(points, spec["duplicate"])
    return _aggregate(contribs, spec, _normalize_aggs(aggs))


def rollup(windows, rule, aggs=None):
    """把上一级窗口结果继续聚合到更粗的窗口，结果与直接降采样一致。

    要求 windows 由 resample 以默认全量聚合（min/max/mean/last）产出。
    """
    spec = _parse_rule(rule)
    contribs = [
        {
            "ts": w["start"],
            "weight": w["count"],
            "min": w["min"],
            "max": w["max"],
            "sum": w["mean"] * w["count"],
            "last": w["last"],
        }
        for w in windows
        if not w["gap"]
    ]
    return _aggregate(contribs, spec, _normalize_aggs(aggs))


def _parse_rule(rule):
    if isinstance(rule, str):
        rule = {"every": rule}
    every = rule.get("every")
    match = _RULE_RE.fullmatch(every.strip().lower()) if isinstance(every, str) else None
    if not match:
        raise ValueError("无法识别的窗口规则: %r" % (every,))
    count, unit = int(match.group(1)), match.group(2)
    if count <= 0:
        raise ValueError("窗口长度必须为正: %r" % (every,))
    spec = {
        "duplicate": rule.get("duplicate", "overwrite"),
        "fill": rule.get("fill", "skip"),
        "origin": rule.get("origin", 0),
    }
    if spec["duplicate"] not in ("overwrite", "accumulate"):
        raise ValueError("duplicate 只能是 overwrite 或 accumulate")
    if spec["fill"] not in ("skip", "pad"):
        raise ValueError("fill 只能是 skip 或 pad")
    if unit in _FIXED_UNITS:
        spec["kind"] = "fixed"
        spec["size"] = count * _FIXED_UNITS[unit]
    else:
        spec["kind"] = "calendar"
        spec["unit"] = unit
        spec["n"] = count
    return spec


def _normalize_aggs(aggs):
    if aggs is None:
        return _AGGS
    if isinstance(aggs, str):
        aggs = (aggs,)
    aggs = tuple(aggs)
    unknown = [a for a in aggs if a not in _AGGS]
    if unknown:
        raise ValueError("未知聚合项: %r" % (unknown,))
    return aggs


def _point_contribs(points, duplicate):
    """乱序点按 ts 归位，重复时间戳按策略覆盖或累加。"""
    merged = {}
    for point in points:
        ts, value = point["ts"], point["v"]
        if duplicate == "accumulate" and ts in merged:
            merged[ts] += value
        else:
            merged[ts] = value
    return [
        {"ts": ts, "weight": 1, "min": v, "max": v, "sum": v, "last": v}
        for ts, v in sorted(merged.items())
    ]


def _window_start(spec, ts):
    if spec["kind"] == "fixed":
        origin, size = spec["origin"], spec["size"]
        return origin + ((ts - origin) // size) * size
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    n = spec["n"]
    unit = spec["unit"]
    if unit == "month":
        index = dt.year * 12 + dt.month - 1
        year, month0 = divmod(index - index % n, 12)
        return int(datetime(year, month0 + 1, 1, tzinfo=timezone.utc).timestamp())
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == "day":
        days = (midnight - _EPOCH).days
        return int((_EPOCH + timedelta(days=days - days % n)).timestamp())
    monday = midnight - timedelta(days=midnight.weekday())
    weeks = (monday - _EPOCH).days // 7
    return int((_EPOCH + timedelta(weeks=weeks - weeks % n)).timestamp())


def _next_start(spec, start):
    if spec["kind"] == "fixed":
        return start + spec["size"]
    dt = datetime.fromtimestamp(start, tz=timezone.utc)
    n, unit = spec["n"], spec["unit"]
    if unit == "day":
        return int((dt + timedelta(days=n)).timestamp())
    if unit == "week":
        return int((dt + timedelta(weeks=n)).timestamp())
    year, month0 = divmod(dt.year * 12 + dt.month - 1 + n, 12)
    return int(datetime(year, month0 + 1, 1, tzinfo=timezone.utc).timestamp())


def _aggregate(contribs, spec, aggs):
    groups = {}
    order = []
    for contrib in contribs:
        start = _window_start(spec, contrib["ts"])
        if start not in groups:
            groups[start] = []
            order.append(start)
        groups[start].append(contrib)
    if not order:
        return []
    if spec["fill"] == "pad":
        padded = []
        current = order[0]
        while current <= order[-1]:
            padded.append(current)
            current = _next_start(spec, current)
        order = padded
    gap_len = {}
    run = []
    for start in order:
        if start in groups:
            if run:
                gap_len.update({s: len(run) for s in run})
                run = []
        else:
            run.append(start)
    gap_len.update({s: len(run) for s in run})
    return [
        _build_window(spec, start, groups.get(start), aggs, gap_len.get(start, 0))
        for start in order
    ]


def _build_window(spec, start, contribs, aggs, gap_len):
    window = {"start": start, "end": _next_start(spec, start)}
    if not contribs:
        window.update(count=0, gap=True, gap_len=gap_len)
        window.update({agg: None for agg in aggs})
        return window
    count = sum(c["weight"] for c in contribs)
    window.update(count=count, gap=False, gap_len=0)
    if "min" in aggs:
        window["min"] = min(c["min"] for c in contribs)
    if "max" in aggs:
        window["max"] = max(c["max"] for c in contribs)
    if "mean" in aggs:
        window["mean"] = sum(c["sum"] for c in contribs) / count
    if "last" in aggs:
        window["last"] = max(contribs, key=lambda c: c["ts"])["last"]
    return window
