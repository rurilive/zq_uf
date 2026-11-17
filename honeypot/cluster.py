"""蜜罐访问行为聚类。

不再按来源地址简单分桶，而是把每个地址的请求路径、时间间隔、
请求头和工具特征提取成行为画像，画像相近的地址合并成一个扫描组：

* 组内记录地址数量、首次/末次时间、提取出的特征和离群事件；
* 偏离组特征的事件单独标为 outliers，无法归入任何组的单地址组
  进入 singletons，方便安全同学单独跟进针对性探测；
* 人工调整（合并组、钉选地址、标注名称）可落盘，下一轮聚类自动套用；
* 同一地址跨多组行为时按主要行为归入一组，其余关系保留在明细里；
* 分组结果按天汇总，便于观察批量扫描的变化。

纯标准库实现，线程间不共享状态，模块级状态通过 reset() 清空。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from statistics import median

# 相似度阈值：加权总分达到该值才并入已有组，否则新开一组。
SIMILARITY_THRESHOLD = 0.60
# 各特征权重，合计 1.0。
W_PATH = 0.40
W_TOOL = 0.35
W_HEADER = 0.15
W_INTERVAL = 0.10
# 特征在组内事件占比达到该值才算核心特征，离群判定围绕核心特征进行。
CORE_SHARE = 0.50
# 相邻请求间隔超过自身中位间隔的倍数即判为时间离群。
INTERVAL_OUTLIER_FACTOR = 10
# 每个成员明细里保留的跨组关系上限：公共路径被大量扫描组共享时，
# 关系展开会随之膨胀，超出上限按组 id 截断，保证最坏情况有界。
MAX_RELATIONS_PER_MEMBER = 20
# 单个特征被超过该比例的组共享时视为无区分度的公共特征（如 /admin），
# 不参与跨组关系构建，避免公共特征制造组对平方且产生无意义关系。
COMMON_FEATURE_GROUP_RATIO = 0.1
# 公共特征判定的绝对下限：组很少时即使比例达标也保留关系。
COMMON_FEATURE_MIN_GROUPS = 5
# 看起来像 epoch 秒的最小时间戳（2001-09-09 之后），否则按相对秒处理。
_EPOCH_THRESHOLD = 1_000_000_000

# UA 子串 -> 工具指纹，全部按小写匹配，顺序即展示顺序。
_TOOL_PATTERNS = (
    "nikto", "sqlmap", "nmap", "masscan", "zmap", "zgrab", "nuclei",
    "gobuster", "dirbuster", "ffuf", "feroxbuster", "dirb", "wpscan",
    "acunetix", "nessus", "metasploit", "hydra", "xray", "httpx",
    "curl", "wget", "python-requests", "go-http-client",
)

# 模块级状态：最近一轮聚类产出与人工调整。
_STATE = {
    "groups": [],          # 最终输出的组字典
    "outliers": [],        # 扁平化离群事件
    "singletons": [],      # 单地址组 id
    "daily": [],           # 全局按天汇总
    "sources": 0,
    "events": 0,
}
_ADJUSTMENTS = {"merges": [], "pins": {}, "labels": {}}
_ADJUST_PATH = None


# ---------------------------------------------------------------- 基础工具

def reset():
    """清空上一轮分组结果和内存中的人工调整。

    已落盘的调整文件不受影响，下一轮用 load_adjustments() 或
    cluster(events, adjustments=路径) 重新载入即可。
    """
    for key in _STATE:
        _STATE[key] = [] if isinstance(_STATE[key], list) else 0
    _ADJUSTMENTS["merges"].clear()
    _ADJUSTMENTS["pins"].clear()
    _ADJUSTMENTS["labels"].clear()
    global _ADJUST_PATH
    _ADJUST_PATH = None


def _extract_tools(ua):
    """从 User-Agent 文本识别工具指纹，识别不到返回空元组。"""
    if not isinstance(ua, str) or not ua:
        return ()
    text = ua.lower()
    return tuple(name for name in _TOOL_PATTERNS if name in text)


def _day_of(at, event):
    """推导事件所属天：优先取事件自带 day 字段，否则按时间戳换算。"""
    day = event.get("day")
    if isinstance(day, str) and day:
        return day
    if isinstance(at, (int, float)) and at >= _EPOCH_THRESHOLD:
        return datetime.fromtimestamp(at, tz=timezone.utc).date().isoformat()
    return "day-%d" % (int(at) // 86400 + 1)


def _weighted_jaccard(left, right):
    """频次加权 Jaccard：sum(min) / sum(max)，两边都为空时视为 1.0。"""
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    overlap = sum(min(c, right.get(k, 0)) for k, c in left.items())
    total = sum(
        max(left.get(k, 0), right.get(k, 0))
        for k in set(left) | set(right)
    )
    return overlap / total if total else 0.0


def _interval_score(left_median, right_median):
    """间隔中位数越接近分数越高，任一缺失时该维度中立（记 1.0）。"""
    if left_median is None or right_median is None:
        return 1.0
    return 1.0 / (1.0 + abs(math.log((left_median + 1) / (right_median + 1))))


# ---------------------------------------------------------------- 行为画像

def _build_profile(ip, rows):
    """把同一地址的事件汇总成行为画像。"""
    rows = sorted(rows, key=lambda e: e["at"])
    path_counts = Counter()
    tool_counts = Counter()
    header_counts = Counter()
    days = Counter()
    gaps = []
    prev_at = None
    for event in rows:
        path_counts[event["path"]] += 1
        for tool in _extract_tools(event.get("ua")):
            tool_counts[tool] += 1
        headers = event.get("headers")
        if isinstance(headers, dict):
            for name in headers:
                header_counts[str(name).lower()] += 1
        days[_day_of(event["at"], event)] += 1
        if prev_at is not None:
            gaps.append(event["at"] - prev_at)
        prev_at = event["at"]
    return {
        "ip": ip,
        "events": len(rows),
        "rows": rows,
        "path_counts": path_counts,
        "tool_counts": tool_counts,
        "header_counts": header_counts,
        "path_set": set(path_counts),
        "tool_set": set(tool_counts),
        "gap_median": median(gaps) if gaps else None,
        "gap_min": min(gaps) if gaps else None,
        "gap_max": max(gaps) if gaps else None,
        "first": rows[0]["at"],
        "last": rows[-1]["at"],
        "days": days,
        "group": None,  # 聚类后指向所属工作组对象
    }


def _new_group(profile):
    return {
        "members": [profile],
        "events": profile["events"],
        "path_counts": Counter(profile["path_counts"]),
        "tool_counts": Counter(profile["tool_counts"]),
        "header_counts": Counter(profile["header_counts"]),
        "path_set": set(profile["path_set"]),
        "tool_set": set(profile["tool_set"]),
        "first": profile["first"],
        "last": profile["last"],
    }


def _absorb(group, profile):
    group["members"].append(profile)
    group["events"] += profile["events"]
    group["path_counts"].update(profile["path_counts"])
    group["tool_counts"].update(profile["tool_counts"])
    group["header_counts"].update(profile["header_counts"])
    group["first"] = min(group["first"], profile["first"])
    group["last"] = max(group["last"], profile["last"])


def _rebuild_group(profiles):
    """成员变动（合并/钉选）后从零重算组聚合数据。"""
    group = _new_group(profiles[0])
    for profile in profiles[1:]:
        _absorb(group, profile)
    return group


def _similarity(profile, group):
    """画像与组（或轻量代表画像）的加权相似度，取值 0~1。"""
    path_score = _weighted_jaccard(profile["path_counts"], group["path_counts"])
    tool_score = _weighted_jaccard(profile["tool_counts"], group["tool_counts"])
    header_score = _weighted_jaccard(
        profile["header_counts"], group["header_counts"]
    )
    if "members" in group:
        group_median = _group_interval_median(group)
    else:
        group_median = group.get("gap_median")
    interval_score = _interval_score(profile["gap_median"], group_median)
    return (
        W_PATH * path_score
        + W_TOOL * tool_score
        + W_HEADER * header_score
        + W_INTERVAL * interval_score
    )


def _group_interval_median(group):
    medians = [m["gap_median"] for m in group["members"] if m["gap_median"] is not None]
    return median(medians) if medians else None


# ---------------------------------------------------------------- 聚类主流程

def _auto_cluster(profiles):
    """代表吸收制聚类，工具指纹硬阻塞，整体近线性。

    每个组固定保留首个成员作为代表（leader），新画像只与候选组的
    代表做完整加权相似度比较——代表是固定小集合，单次判定成本恒定，
    不随组成员数增长。候选由工具倒排索引（无工具时退回路径索引）
    限定在同特征块内。吸收只累加聚合统计、不改变代表，避免公共
    特征下每个新画像都重算大组的平方膨胀。
    """
    profiles.sort(key=lambda p: (-p["events"], p["ip"]))
    groups = []
    path_index = {}
    tool_index = {}
    for profile in profiles:
        if profile["tool_set"]:
            candidates = set()
            for tool in profile["tool_set"]:
                candidates.update(tool_index.get(tool, ()))
        else:
            candidates = set()
            for path in profile["path_set"]:
                candidates.update(path_index.get(path, ()))
        best = None
        best_score = -1.0
        for idx in candidates:
            group = groups[idx]
            # 只与固定大小的代表画像比，单次判定成本不随组规模增长。
            score = _similarity(profile, group["leader"])
            if score > best_score:
                best, best_score = idx, score
        if best is not None and best_score >= SIMILARITY_THRESHOLD:
            _absorb(groups[best], profile)
            profile["group"] = groups[best]
            target = groups[best]
        else:
            group = _new_group(profile)
            group["leader"] = {
                "path_set": set(profile["path_set"]),
                "tool_set": set(profile["tool_set"]),
                "path_counts": Counter(profile["path_counts"]),
                "tool_counts": Counter(profile["tool_counts"]),
                "header_counts": Counter(profile["header_counts"]),
                "gap_median": profile["gap_median"],
            }
            profile["group"] = group
            groups.append(group)
            target = group
            idx = len(groups) - 1
            for path in profile["path_set"]:
                path_index.setdefault(path, set()).add(idx)
            for tool in profile["tool_set"]:
                tool_index.setdefault(tool, set()).add(idx)
        target["path_set"] = set(target["path_counts"])
        target["tool_set"] = set(target["tool_counts"])
    return groups


# ---------------------------------------------------------------- 人工调整

def _resolve_ref(ref, wgroups):
    """按临时 id / 签名 / 标注名解析到工作组，解析不到抛 ValueError。"""
    for group in wgroups:
        if ref in (group["id"], group["signature"], group.get("label")):
            return group
    raise ValueError("unknown group reference: %r" % (ref,))


def _refresh(group):
    """成员变化后原地重算聚合数据，保留 id / label。"""
    members = group["members"]
    group["events"] = sum(m["events"] for m in members)
    group["path_counts"] = sum((m["path_counts"] for m in members), Counter())
    group["tool_counts"] = sum((m["tool_counts"] for m in members), Counter())
    group["header_counts"] = sum((m["header_counts"] for m in members), Counter())
    group["path_set"] = set(group["path_counts"])
    group["tool_set"] = set(group["tool_counts"])
    group["first"] = min(m["first"] for m in members)
    group["last"] = max(m["last"] for m in members)
    group["signature"] = _signature(group)
    for profile in members:
        profile["group"] = group


def _apply_adjustments(groups, adjustments):
    """在自动聚类结果上套用人工调整：先标注，再合并，最后钉选。"""
    # 快路径：没有任何人工调整时只编号，不做成员重扫与并查集，
    # 保证日常无调整轮次的开销与来源数解耦。
    has_adjustment = any((
        adjustments.get("labels"),
        adjustments.get("merges"),
        adjustments.get("pins"),
    ))
    if not has_adjustment:
        for idx, group in enumerate(groups):
            group["id"] = "G%04d" % (idx + 1)
            group["signature"] = _signature(group)
            group["label"] = None
        return groups

    wgroups = []
    for idx, group in enumerate(groups):
        group["id"] = "G%04d" % (idx + 1)
        group["signature"] = _signature(group)
        group["label"] = None
        wgroups.append(group)

    # 1) 标注名先挂到自动组上，后续合并/钉选即可按名字引用。
    for ref, label in adjustments.get("labels", {}).items():
        _resolve_ref(ref, wgroups)["label"] = label

    # 2) 合并：并查集收敛后重建组，人工名优先，否则沿用成员组已有名字。
    parent = {g["id"]: g["id"] for g in wgroups}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    merge_labels = {}
    for rule in adjustments.get("merges", []):
        target = _resolve_ref(rule["into"], wgroups)
        roots = {find(target["id"])}
        for ref in rule["sources"]:
            source = _resolve_ref(ref, wgroups)
            roots.add(find(source["id"]))
        roots = sorted(roots)
        for root in roots[1:]:
            parent[root] = roots[0]
        if rule.get("label"):
            merge_labels[find(target["id"])] = rule["label"]

    merged = {}
    for group in wgroups:
        merged.setdefault(find(group["id"]), []).append(group)
    wgroups = []
    for root, parts in merged.items():
        members = [profile for part in parts for profile in part["members"]]
        members.sort(key=lambda p: (-p["events"], p["ip"]))
        rebuilt = _rebuild_group(members)
        rebuilt["id"] = root
        rebuilt["label"] = (
            merge_labels.get(root)
            or next((part["label"] for part in parts if part.get("label")), None)
        )
        rebuilt["signature"] = _signature(rebuilt)
        wgroups.append(rebuilt)
    for profile in (p for g in wgroups for p in g["members"]):
        profile["group"] = None

    # 3) 钉选：把地址整体移到指定组，主行为随之改变。
    for ip, ref in adjustments.get("pins", {}).items():
        target = _resolve_ref(ref, wgroups)
        profile = next((p for g in wgroups for p in g["members"] if p["ip"] == ip), None)
        if profile is None:
            continue
        source = next(g for g in wgroups if profile in g["members"])
        if source is target:
            continue
        source["members"].remove(profile)
        target["members"].append(profile)
        _refresh(target)
        if source["members"]:
            _refresh(source)
        else:
            wgroups.remove(source)
    for profile in (p for g in wgroups for p in g["members"]):
        profile["group"] = next(g for g in wgroups if profile in g["members"])
    return wgroups


def adjust(action, **kwargs):
    """登记一条人工调整，下一轮 cluster() 自动套用。

    支持的动作：
    * merge: sources=[组引用...], into=组引用, label=可选名；
    * pin: ip=地址, group=组引用，把地址钉进指定组；
    * label: group=组引用, label=名称；
    * unmerge / unpin / unlabel: 撤销对应调整。
    组引用可以是组 id、签名或人工标注名。
    """
    if action == "merge":
        sources = kwargs.get("sources")
        into = kwargs.get("into")
        if not sources or not into:
            raise ValueError("merge needs sources and into")
        _ADJUSTMENTS["merges"].append(
            {"sources": list(sources), "into": into, "label": kwargs.get("label")}
        )
    elif action == "pin":
        if not kwargs.get("ip") or not kwargs.get("group"):
            raise ValueError("pin needs ip and group")
        _ADJUSTMENTS["pins"][kwargs["ip"]] = kwargs["group"]
    elif action == "label":
        if not kwargs.get("group") or kwargs.get("label") is None:
            raise ValueError("label needs group and label")
        _ADJUSTMENTS["labels"][kwargs["group"]] = kwargs["label"]
    elif action == "unpin":
        _ADJUSTMENTS["pins"].pop(kwargs.get("ip"), None)
    elif action == "unlabel":
        _ADJUSTMENTS["labels"].pop(kwargs.get("group"), None)
    elif action == "unmerge":
        before = len(_ADJUSTMENTS["merges"])
        _ADJUSTMENTS["merges"] = [
            rule for rule in _ADJUSTMENTS["merges"]
            if rule["into"] != kwargs.get("into")
            or set(rule["sources"]) != set(kwargs.get("sources", []))
        ]
        if len(_ADJUSTMENTS["merges"]) == before:
            raise ValueError("no matching merge to remove")
    else:
        raise ValueError("unknown adjustment action: %r" % (action,))
    if _ADJUST_PATH:
        save_adjustments(_ADJUST_PATH)
    return dict(_ADJUSTMENTS)


def load_adjustments(path):
    """从 JSON 文件载入人工调整。"""
    global _ADJUST_PATH
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    _ADJUSTMENTS["merges"] = list(data.get("merges", []))
    _ADJUSTMENTS["pins"] = dict(data.get("pins", {}))
    _ADJUSTMENTS["labels"] = dict(data.get("labels", {}))
    _ADJUST_PATH = path
    return dict(_ADJUSTMENTS)


def save_adjustments(path=None):
    """把人工调整落盘，返回写入的路径。"""
    global _ADJUST_PATH
    path = path or _ADJUST_PATH
    if not path:
        raise ValueError("no adjustment path given")
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_ADJUSTMENTS, fh, ensure_ascii=False, indent=2, sort_keys=True)
    _ADJUST_PATH = path
    return path


def clear_adjustments():
    """清空内存中的人工调整（不动已落盘文件）。"""
    _ADJUSTMENTS["merges"].clear()
    _ADJUSTMENTS["pins"].clear()
    _ADJUSTMENTS["labels"].clear()


# ---------------------------------------------------------------- 组定稿

def _core_features(counts, total):
    """核心特征：事件占比达标，若无一达标则退化为出现最多的那一个。"""
    core = {name for name, count in counts.items() if count / total >= CORE_SHARE}
    if not core and counts:
        core = {max(counts, key=lambda k: (counts[k], k))}
    return core


def _interval_bucket(median_value):
    if median_value is None:
        return "na"
    return str(2 ** int(math.log2(max(median_value, 1))))


def _signature(group):
    """组签名：核心路径 + 核心工具 + 间隔档位的稳定哈希，跨轮可复算。"""
    core_paths = _core_features(group["path_counts"], group["events"])
    core_tools = _core_features(group["tool_counts"], group["events"])
    raw = "%s|%s|%s" % (
        ",".join(sorted(core_paths)),
        ",".join(sorted(core_tools)),
        _interval_bucket(_group_interval_median(group)),
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _auto_label(group):
    tools = sorted(group["tool_counts"])
    if not tools:
        return "generic-scan"
    if len(tools) <= 2:
        return "%s-scan" % "-".join(tools)
    return "%s-scan" % "-".join(tools[:2])


def _feature_entries(counts, total, core):
    return [
        {"name": name, "events": counts[name], "share": round(counts[name] / total, 4),
         "core": name in core}
        for name in sorted(counts, key=lambda k: (-counts[k], k))
    ]


def _find_outliers(group):
    """逐事件检查偏离组特征的请求，离群事件仍保留在组统计内。

    参照面是同组其他成员的特征并集：成员自己惯常访问的路径不影响判定，
    只要同组其他地址也打就属于该扫描组的行为面；只有所有同伴都没
    出现过的特征才判为偏离，避免批量扫描的长尾路径制造噪音、
    淹没真正的针对性探测。单地址组退化为按自身全部特征判断。
    同伴并集用频次表增量维护，避免对每个成员重建一次集合。
    """
    total_paths = Counter()
    total_tools = Counter()
    total_headers = Counter()
    for member in group["members"]:
        total_paths.update(member["path_counts"])
        total_tools.update(member["tool_counts"])
        total_headers.update(member["header_counts"])
    multi = len(group["members"]) > 1
    outliers = []
    for profile in group["members"]:
        if multi:
            peer_paths = {k for k, c in total_paths.items()
                          if c - profile["path_counts"].get(k, 0) > 0}
            peer_tools = {k for k, c in total_tools.items()
                          if c - profile["tool_counts"].get(k, 0) > 0}
            peer_headers = {k for k, c in total_headers.items()
                            if c - profile["header_counts"].get(k, 0) > 0}
        else:
            peer_paths, peer_tools, peer_headers = (
                set(total_paths), set(total_tools), set(total_headers))
        previous_at = None
        for event in profile["rows"]:
            reasons = []
            if event["path"] not in peer_paths:
                reasons.append("path")
            tools = _extract_tools(event.get("ua"))
            if peer_tools and tools and not set(tools) & peer_tools:
                reasons.append("tool")
            headers = event.get("headers")
            if isinstance(headers, dict) and peer_headers:
                if not {str(n).lower() for n in headers} & peer_headers:
                    reasons.append("header")
            if (
                previous_at is not None
                and profile["gap_median"] is not None
                and event["at"] - previous_at >= INTERVAL_OUTLIER_FACTOR * profile["gap_median"]
            ):
                reasons.append("interval")
            if reasons:
                outliers.append({
                    "ip": profile["ip"],
                    "at": event["at"],
                    "path": event["path"],
                    "ua": event.get("ua"),
                    "reasons": reasons,
                })
            previous_at = event["at"]
    outliers.sort(key=lambda e: (e["at"], e["ip"], e["path"]))
    return outliers


def _group_daily(group):
    day_events = Counter()
    day_sources = {}
    for profile in group["members"]:
        for day, count in profile["days"].items():
            day_events[day] += count
            day_sources.setdefault(day, set()).add(profile["ip"])
    return [
        {"day": day, "events": day_events[day], "sources": len(day_sources[day])}
        for day in sorted(day_events)
    ]


def _public(group):
    """对外输出时去掉内部画像等私有字段。"""
    return {key: value for key, value in group.items() if not key.startswith("_")}


def _finalize(wgroups):
    """重排、编号并把工作组转成对外输出结构，同时补全跨组关系。"""
    wgroups.sort(key=lambda g: (-g["events"], g["first"], g["signature"]))
    for idx, group in enumerate(wgroups):
        group["id"] = "G%04d" % (idx + 1)
        group["signature"] = _signature(group)
        group.setdefault("label", None)

    by_id = {g["id"]: g for g in wgroups}

    # 跨组关系：组级倒排索引先在组维度配对（每个特征与含它的组数同阶），
    # 成员是否命中再用画像集合交集判定。公共路径被大量扫描组共享时
    # 组对会膨胀，因此每个成员保留的关系组数量设上限，保证展开有界。
    relation_map = {}
    path_owners = {}
    tool_owners = {}
    for group in wgroups:
        for path in group["path_set"]:
            path_owners.setdefault(path, set()).add(group["id"])
        for tool in group["tool_set"]:
            tool_owners.setdefault(tool, set()).add(group["id"])

    def _scan(owners, kind):
        """每个特征只在组维度配对一次，成本与含该特征的组数同阶。

        被超过 COMMON_FEATURE_GROUP_RATIO 的组共享的特征无区分度，
        跳过不建关系，从源头消除公共特征导致的组对平方。
        """
        group_total = max(len(wgroups), 1)
        for feature, group_ids in owners.items():
            if (
                len(group_ids) >= COMMON_FEATURE_MIN_GROUPS
                and len(group_ids) / group_total > COMMON_FEATURE_GROUP_RATIO
            ):
                continue
            ids = sorted(group_ids)
            for pos, left_id in enumerate(ids):
                for right_id in ids[pos + 1:]:
                    relation_map.setdefault(
                        (left_id, right_id),
                        {"paths": set(), "tools": set()})[kind].add(feature)
                    relation_map.setdefault(
                        (right_id, left_id),
                        {"paths": set(), "tools": set()})[kind].add(feature)

    _scan(path_owners, "paths")
    _scan(tool_owners, "tools")

    # 组 id -> 按 id 排序的关联组，供成员展开时按上限截断。
    related_groups = {}
    for (left_id, right_id) in relation_map:
        related_groups.setdefault(left_id, set()).add(right_id)

    output = []
    for group in wgroups:
        core_paths = _core_features(group["path_counts"], group["events"])
        core_tools = _core_features(group["tool_counts"], group["events"])
        core_headers = _core_features(group["header_counts"], group["events"])
        outliers = _find_outliers(group)

        # 与本组有共享特征的其他组（组维度已在倒排扫描中配对完成）。
        other_ids = sorted(related_groups.get(group["id"], ()))
        relations = []
        member_view = []
        for profile in sorted(group["members"], key=lambda p: (-p["events"], p["ip"])):
            also_in = []
            for other_id in other_ids:
                if len(also_in) >= MAX_RELATIONS_PER_MEMBER:
                    break
                other = by_id[other_id]
                # 成员是否真的命中该共享特征，用集合交集判定。
                shared_paths = sorted(profile["path_set"] & other["path_set"])
                shared_tools = sorted(profile["tool_set"] & other["tool_set"])
                if not (shared_paths or shared_tools):
                    continue
                also_in.append(other_id)
                relations.append({
                    "ip": profile["ip"],
                    "group_id": other_id,
                    "label": other["label"] or _auto_label(other),
                    "shared_paths": shared_paths,
                    "shared_tools": shared_tools,
                    "events": profile["events"],
                })
            member_view.append({
                "ip": profile["ip"],
                "events": profile["events"],
                "first": profile["first"],
                "last": profile["last"],
                "also_in": also_in,
            })

        gap_medians = [m["gap_median"] for m in group["members"] if m["gap_median"] is not None]
        gap_mins = [m["gap_min"] for m in group["members"] if m["gap_min"] is not None]
        gap_maxs = [m["gap_max"] for m in group["members"] if m["gap_max"] is not None]
        days = sorted({d for m in group["members"] for d in m["days"]})
        output.append({
            "id": group["id"],
            "label": group["label"] or _auto_label(group),
            "signature": group["signature"],
            "singleton": len(group["members"]) == 1,
            "sources": len(group["members"]),
            "events": group["events"],
            "first": group["first"],
            "last": group["last"],
            "days": days,
            "features": {
                "paths": _feature_entries(group["path_counts"], group["events"], core_paths),
                "tools": _feature_entries(group["tool_counts"], group["events"], core_tools),
                "headers": _feature_entries(group["header_counts"], group["events"], core_headers),
                "interval_median": median(gap_medians) if gap_medians else None,
                "interval_min": min(gap_mins) if gap_mins else None,
                "interval_max": max(gap_maxs) if gap_maxs else None,
            },
            "members": member_view,
            "outliers": outliers,
            "relations": sorted(relations, key=lambda r: (r["ip"], r["group_id"])),
            "daily": _group_daily(group),
            "_profiles": list(group["members"]),
        })
    return output


def _global_daily(groups_out):
    """全局按天汇总，数据直接取各组定稿时保留的成员画像。"""
    day_events = Counter()
    day_sources = {}
    day_groups = {}
    for group in groups_out:
        for row in group["daily"]:
            day = row["day"]
            day_events[day] += row["events"]
            day_groups.setdefault(day, set()).add(group["id"])
        for profile in group["_profiles"]:
            for day in profile["days"]:
                day_sources.setdefault(day, set()).add(profile["ip"])
    return [
        {"day": day, "groups": len(day_groups[day]),
         "sources": len(day_sources[day]), "events": day_events[day]}
        for day in sorted(day_events)
    ]


# ---------------------------------------------------------------- 对外入口

def cluster(events, adjustments=None):
    """对一批访问事件做行为聚类，返回扫描组与离群事件。

    events: 事件字典列表，每个事件至少含 ip / path / at，
            可选 ua、headers（字典）、day（字符串）。
    adjustments: 人工调整来源——字典、JSON 文件路径，或 None（沿用内存）。
    """
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    buckets = {}
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event must be a dict")
        for key in ("ip", "path", "at"):
            if event.get(key) is None:
                raise ValueError("event needs ip, path and at")
        buckets.setdefault(event["ip"], []).append(event)

    global _ADJUST_PATH
    if isinstance(adjustments, (str, os.PathLike)):
        load_adjustments(adjustments)
    elif isinstance(adjustments, dict):
        _ADJUSTMENTS["merges"] = list(adjustments.get("merges", []))
        _ADJUSTMENTS["pins"] = dict(adjustments.get("pins", {}))
        _ADJUSTMENTS["labels"] = dict(adjustments.get("labels", {}))

    profiles = [_build_profile(ip, rows) for ip, rows in buckets.items()]
    auto_groups = _auto_cluster(profiles)
    wgroups = _apply_adjustments(auto_groups, _ADJUSTMENTS)
    groups_out = _finalize(wgroups)

    flat_outliers = [
        dict(outlier, group_id=group["id"])
        for group in groups_out for outlier in group["outliers"]
    ]
    _STATE["groups"] = groups_out
    _STATE["outliers"] = flat_outliers
    _STATE["singletons"] = [g["id"] for g in groups_out if g["singleton"]]
    _STATE["daily"] = _global_daily(groups_out)
    _STATE["sources"] = len(buckets)
    _STATE["events"] = len(events)
    return {
        "groups": [_public(group) for group in groups_out],
        "groups_count": len(groups_out),
        "sources": len(buckets),
        "events": len(events),
        "outliers": flat_outliers,
        "singletons": list(_STATE["singletons"]),
    }


def report():
    """按访问条数从多到少给出分组，并附离群、单地址组和按天汇总。"""
    groups = _STATE["groups"]
    return {
        "groups": len(groups),
        "sources": _STATE["sources"],
        "events": _STATE["events"],
        "top": [_public(group) for group in sorted(
            groups, key=lambda g: (-g["events"], g["id"]))],
        "outliers": [dict(item) for item in _STATE["outliers"]],
        "singletons": list(_STATE["singletons"]),
        "daily": [dict(row) for row in _STATE["daily"]],
        "adjustments": {
            "merges": len(_ADJUSTMENTS["merges"]),
            "pins": len(_ADJUSTMENTS["pins"]),
            "labels": len(_ADJUSTMENTS["labels"]),
        },
    }
