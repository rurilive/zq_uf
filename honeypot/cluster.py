"""蜜罐访问行为聚类。

按请求路径、时间间隔和工具特征把批量扫描归成一组：
组内记录地址数量与首次/末次时间，偏离组特征的事件单独标为离群。
同一地址跨多组出现时按主要行为（事件数最多）归入一组，
其余关系保留在该组的 shared 明细里。结果按天汇总。
"""

import re
from datetime import datetime, timezone

_GROUPS = []
_OUTLIERS = []
_DAILY = []
_STATS = {"sources": 0, "events": 0}

_KNOWN_TOOLS = (
    "nikto", "gobuster", "sqlmap", "nmap", "masscan", "nuclei", "acunetix",
    "hydra", "dirb", "wpscan", "zmap", "python-requests", "scrapy", "curl",
    "wget",
)

# 路径特征集合的 Jaccard 相似度达到该阈值即并为同一扫描组
_MERGE_JACCARD = 0.5


def reset():
    """清空上一轮分组结果。"""
    _GROUPS.clear()
    _OUTLIERS.clear()
    _DAILY.clear()
    _STATS.update({"sources": 0, "events": 0})


def cluster(events, prior=None, adjust=None):
    """按行为特征把访问事件聚成扫描组。

    events: 事件列表，每条至少含 ip、path、at，可含 ua/headers/tool。
    prior:  上一轮 cluster() 的返回结果，命中的组沿用原组 id，
            便于把人工看过的分组带到下一轮。
    adjust: 人工调整，{"merge": [[idA, idB], ...], "move": {ip: 组id}}。
            merge 把多组合并到第一个 id；move 把某地址改归指定组。
    返回 {"groups", "outliers", "sources", "events", "daily"}。
    """
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event must be a dict")
        for key in ("ip", "path", "at"):
            if event.get(key) is None:
                raise ValueError("event needs ip, path and at")

    segments = _segments(events)
    groups = _merge_similar(_buckets(segments))
    _assign_ids(groups, prior)
    if adjust:
        _apply_adjust(groups, adjust)
    _finalize(groups, events)
    return report()


def report():
    """返回上一轮分组结果：扫描组、离群事件和按天汇总。"""
    return {
        "groups": [dict(group) for group in _GROUPS],
        "outliers": [dict(item) for item in _OUTLIERS],
        "sources": _STATS["sources"],
        "events": _STATS["events"],
        "daily": [dict(row) for row in _DAILY],
    }


# ---------------------------------------------------------------- 特征提取

def _tool_of(event):
    """工具特征：显式 tool 字段优先，否则从 ua / User-Agent 提取。"""
    tool = event.get("tool")
    ua = event.get("ua") or ""
    if not ua:
        headers = event.get("headers") or {}
        ua = headers.get("User-Agent") or headers.get("user-agent") or ""
    text = (str(tool) if tool else str(ua)).strip().lower()
    if not text:
        return "unknown"
    for name in _KNOWN_TOOLS:
        if name in text:
            return name
    if tool:
        return str(tool).strip().lower()
    return re.split(r"[\s/;(]", text, 1)[0] or "unknown"


def _path_pattern(path):
    """路径归一化：去查询串，数字段替换为 {n}。"""
    return re.sub(r"\d+", "{n}", str(path).split("?", 1)[0])


def _median_interval(rows):
    """同一来源相邻请求间隔的中位数，单条返回 None。

    偶数个间隔取靠左的中项，避免个别离群间隔（如针对性探测的长停顿）
    把整体节奏拉偏。
    """
    if len(rows) < 2:
        return None
    ats = sorted(row["at"] for row in rows)
    gaps = sorted(b - a for a, b in zip(ats, ats[1:]))
    return gaps[(len(gaps) - 1) // 2]


def _interval_bucket(interval):
    if interval is None:
        return None
    if interval < 2:
        return 1
    if interval < 15:
        return 10
    if interval < 90:
        return 60
    if interval < 600:
        return 300
    return 3600


def _day_of(at):
    if isinstance(at, (int, float)):
        return datetime.fromtimestamp(at, tz=timezone.utc).strftime("%Y-%m-%d")
    return str(at)[:10]


# ---------------------------------------------------------------- 聚类

def _segments(events):
    """每个来源按工具切成行为段，段是参与聚类的最小单位。"""
    by_source = {}
    for event in events:
        by_source.setdefault(event["ip"], []).append(event)
    segments = []
    for ip in sorted(by_source):
        rows = sorted(by_source[ip], key=lambda row: (row["at"], str(row["path"])))
        by_tool = {}
        for row in rows:
            by_tool.setdefault(_tool_of(row), []).append(row)
        for tool in sorted(by_tool):
            tool_rows = by_tool[tool]
            segments.append({
                "ip": ip,
                "tool": tool,
                "rows": tool_rows,
                "patterns": sorted({_path_pattern(row["path"]) for row in tool_rows}),
                "interval": _median_interval(tool_rows),
            })
    return segments


def _buckets(segments):
    """特征完全相同的段先合成桶。"""
    buckets = {}
    for seg in segments:
        key = (seg["tool"], _interval_bucket(seg["interval"]), tuple(seg["patterns"]))
        bucket = buckets.setdefault(key, {
            "tool": seg["tool"],
            "interval_bucket": _interval_bucket(seg["interval"]),
            "patterns": list(seg["patterns"]),
            "segments": [],
        })
        bucket["segments"].append(seg)
    return list(buckets.values())


def _jaccard(left, right):
    union = len(set(left) | set(right))
    return len(set(left) & set(right)) / union if union else 1.0


def _merge_similar(buckets):
    """工具与间隔档位相同、路径特征相近的桶并成一个扫描组。"""
    groups = []
    ordered = sorted(
        buckets,
        key=lambda b: (-sum(len(s["rows"]) for s in b["segments"]),
                       b["tool"], str(b["interval_bucket"]), b["patterns"]),
    )
    for bucket in ordered:
        for group in groups:
            if (group["tool"] == bucket["tool"]
                    and group["interval_bucket"] == bucket["interval_bucket"]
                    and _jaccard(group["patterns"], bucket["patterns"]) >= _MERGE_JACCARD):
                group["segments"].extend(bucket["segments"])
                group["patterns"] = sorted(set(group["patterns"]) | set(bucket["patterns"]))
                break
        else:
            groups.append({
                "tool": bucket["tool"],
                "interval_bucket": bucket["interval_bucket"],
                "patterns": list(bucket["patterns"]),
                "segments": list(bucket["segments"]),
            })
    return groups


def _assign_ids(groups, prior):
    """与上一轮特征相同的组沿用原 id，其余分配新 id。"""
    prior_ids = {}
    if prior:
        prior_groups = prior.get("groups", prior) if isinstance(prior, dict) else prior
        for item in prior_groups:
            features = item.get("features", {})
            key = (features.get("tool"),
                   features.get("interval_bucket"),
                   tuple(features.get("paths", ())))
            if item.get("id"):
                prior_ids.setdefault(key, item["id"])
    used = set()
    for group in groups:
        key = (group["tool"], group["interval_bucket"], tuple(group["patterns"]))
        gid = prior_ids.get(key)
        if gid and gid not in used:
            group["id"] = gid
            used.add(gid)
    n = 1
    for group in groups:
        if "id" in group:
            continue
        while "g%d" % n in used:
            n += 1
        group["id"] = "g%d" % n
        used.add("g%d" % n)


def _apply_adjust(groups, adjust):
    """人工调整：merge 合并组，move 把地址改归指定组。"""
    by_id = {group["id"]: group for group in groups}
    for pair in adjust.get("merge", []):
        ids = list(pair)
        target = by_id.get(ids[0]) if ids else None
        if target is None:
            raise ValueError("unknown group id in merge: %r" % (ids[0] if ids else None))
        for other_id in ids[1:]:
            other = by_id.pop(other_id, None)
            if other is None:
                raise ValueError("unknown group id in merge: %r" % other_id)
            groups.remove(other)
            target["segments"].extend(other["segments"])
            target["patterns"] = sorted(set(target["patterns"]) | set(other["patterns"]))
    moves = adjust.get("move", {})
    for ip, gid in moves.items():
        if gid not in by_id:
            raise ValueError("unknown group id in move: %r" % gid)
        target = by_id[gid]
        for group in groups:
            if group["id"] == gid:
                continue
            moved = [seg for seg in group["segments"] if seg["ip"] == ip]
            if moved:
                group["segments"] = [seg for seg in group["segments"] if seg["ip"] != ip]
                target["segments"].extend(moved)
        # move 是显式指定主要行为，优先于按事件数的自动归属
        target.setdefault("pinned", set()).add(ip)


# ---------------------------------------------------------------- 汇总

def _finalize(groups, events):
    groups[:] = [group for group in groups if group["segments"]]

    # 每个地址按事件数最多的组作为主要行为，其余组记入 shared 明细
    ip_counts = {}
    for group in groups:
        for seg in group["segments"]:
            counts = ip_counts.setdefault(seg["ip"], {})
            counts[group["id"]] = counts.get(group["id"], 0) + len(seg["rows"])
    primary = {}
    for ip, counts in ip_counts.items():
        primary[ip] = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    for group in groups:
        for ip in group.get("pinned", ()):
            if ip in primary:
                primary[ip] = group["id"]

    _GROUPS.clear()
    _OUTLIERS.clear()
    daily = {}

    ordered = sorted(groups, key=lambda g: (
        -sum(len(s["rows"]) for s in g["segments"]), g["id"]))
    for group in ordered:
        rows = [row for seg in group["segments"] for row in seg["rows"]]
        ips = sorted({seg["ip"] for seg in group["segments"]})
        sources = [ip for ip in ips if primary.get(ip) == group["id"]]
        shared = [ip for ip in ips if primary.get(ip) != group["id"]]
        intervals = [seg["interval"] for seg in group["segments"]
                     if seg["interval"] is not None]
        interval = _median_of(intervals)

        # 组特征：两个及以上来源共有的路径模式视为组签名
        pattern_hits = {}
        for seg in group["segments"]:
            for pattern in seg["patterns"]:
                pattern_hits.setdefault(pattern, set()).add(seg["ip"])
        common = {pattern for pattern, owners in pattern_hits.items()
                  if len(owners) >= 2}

        for row in rows:
            tool = _tool_of(row)
            pattern = _path_pattern(row["path"])
            reason = None
            if tool != group["tool"]:
                reason = "tool %s 偏离组特征 %s" % (tool, group["tool"])
            elif len(ips) >= 2 and common and pattern not in common:
                reason = "path %s 不在组签名内" % pattern
            if reason:
                _OUTLIERS.append({
                    "ip": row["ip"], "path": row["path"], "at": row["at"],
                    "tool": tool, "group": group["id"], "reason": reason,
                })

        for row in rows:
            day = _day_of(row["at"])
            slot = daily.setdefault(day, {"events": 0, "sources": set(), "groups": set()})
            slot["events"] += 1
            slot["sources"].add(row["ip"])
            slot["groups"].add(group["id"])

        _GROUPS.append({
            "id": group["id"],
            "features": {
                "paths": list(group["patterns"]),
                "path_count": len(group["patterns"]),
                "tool": group["tool"],
                "interval": interval,
                "interval_bucket": group["interval_bucket"],
            },
            "sources": sources,
            "source_count": len(sources),
            "shared": shared,
            "count": len(rows),
            "first": min(row["at"] for row in rows),
            "last": max(row["at"] for row in rows),
        })

    _DAILY.clear()
    for day in sorted(daily):
        slot = daily[day]
        _DAILY.append({
            "day": day,
            "events": slot["events"],
            "sources": len(slot["sources"]),
            "groups": len(slot["groups"]),
        })

    _STATS["sources"] = len({event["ip"] for event in events})
    _STATS["events"] = len(events)


def _median_of(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2
