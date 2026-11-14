"""站点结构指纹比对。

快照把链接按“栏目 + 深度”归一成结构指纹；比对时输出指纹、链接差异
和按栏目抽样的样本。告警分三类：新增栏目、层级变化、大量删除。
同一告警连续出现达到 ``escalate_after`` 批后由 ``warning`` 升级为 ``alert``。

对外接口保持 ``snapshot(sitemap)`` 和 ``diff(old, new)``，两者都接受
可选的 ``config``；``diff`` 另外接受可选的 ``state`` 以累计连续批次，
常用的有状态封装是 :class:`Tracker`。
"""

import hashlib
import json
from collections import Counter, defaultdict

ROOT_SECTION = "(root)"
FINGERPRINT_VERSION = 1

DEFAULT_CONFIG = {
    # 栏目取 URL 路径的前几段（1 表示一级栏目）。
    "section_level": 1,
    # 每个栏目每次最多输出的样本链接数。
    "sample_size": 3,
    # 新增栏目的最少链接数，低于该值视为噪声。
    "new_section_min_links": 1,
    # 层级变化的最少受影响链接数；单个链接的深度变化更可能是内容更新。
    "level_change_min_links": 2,
    # 大量删除：删除数 >= min 且删除比例 >= ratio 才告警。
    "bulk_removal_ratio": 0.2,
    "bulk_removal_min": 10,
    # 同一告警连续出现多少批后升级为 alert。
    "escalate_after": 2,
}

_ALERT_ORDER = {"new_section": 0, "level_change": 1, "bulk_removal": 2}


def _merge_config(config=None):
    cfg = dict(DEFAULT_CONFIG)
    if config:
        unknown = sorted(set(config) - set(DEFAULT_CONFIG))
        if unknown:
            raise ValueError("未知配置项: %s" % ", ".join(unknown))
        cfg.update(config)
    if cfg["section_level"] < 1:
        raise ValueError("section_level 必须 >= 1")
    if cfg["sample_size"] < 1:
        raise ValueError("sample_size 必须 >= 1")
    if cfg["new_section_min_links"] < 1:
        raise ValueError("new_section_min_links 必须 >= 1")
    if cfg["level_change_min_links"] < 1:
        raise ValueError("level_change_min_links 必须 >= 1")
    if cfg["bulk_removal_min"] < 1:
        raise ValueError("bulk_removal_min 必须 >= 1")
    ratio = cfg["bulk_removal_ratio"]
    if not isinstance(ratio, (int, float)) or not 0 < ratio <= 1:
        raise ValueError("bulk_removal_ratio 必须满足 0 < ratio <= 1")
    if cfg["escalate_after"] < 1:
        raise ValueError("escalate_after 必须 >= 1")
    return cfg


def _path_segments(link):
    """从链接中取出路径段，去掉协议、主机、查询串和尾斜杠。"""
    text = str(link).strip()
    if not text:
        return []
    text = text.split("#", 1)[0].split("?", 1)[0]
    if "://" in text:
        text = text.split("://", 1)[1]
        slash = text.find("/")
        text = text[slash:] if slash >= 0 else ""
    elif text.startswith("//"):
        text = text[2:]
        slash = text.find("/")
        text = text[slash:] if slash >= 0 else ""
    text = text.replace("\\", "/")
    segments = [seg for seg in text.split("/") if seg and seg != "."]
    if segments and segments[-1].lower() in ("index.html", "index.htm", "index.php"):
        segments.pop()
    return segments


def _section_name(segments, level):
    if not segments:
        return ROOT_SECTION
    return "/".join(segments[:level])


def _section_of(link, level):
    return _section_name(_path_segments(link), level)


def _section_depth(name):
    return 0 if name == ROOT_SECTION else name.count("/") + 1


def _section_parent(name):
    if name == ROOT_SECTION or "/" not in name:
        return ""
    return name.rsplit("/", 1)[0]


def _digest(payload):
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def _sample(items, size):
    """稳定抽样：排序后等距取点，结果可重复。"""
    ordered = sorted(set(items))
    if len(ordered) <= size:
        return ordered
    if size == 1:
        return [ordered[0]]
    step = (len(ordered) - 1) / float(size - 1)
    picked = []
    for index in range(size):
        item = ordered[int(round(index * step))]
        if item not in picked:
            picked.append(item)
    return picked


def _group_by_section(links, level):
    grouped = defaultdict(list)
    for link in links:
        grouped[_section_of(link, level)].append(link)
    return grouped


def _link_under(link, node):
    segments = _path_segments(link)
    node_depth = _section_depth(node)
    if node_depth == 0 or len(segments) < node_depth:
        return False
    return "/".join(segments[:node_depth]) == node


def _tree_nodes(segments, section):
    """链接经过的目录节点；栏目落地页仍保留栏目节点。"""
    nodes = ["/".join(segments[:index]) for index in range(1, len(segments))]
    if section != ROOT_SECTION and section not in nodes:
        nodes.append(section)
    return nodes


def snapshot(sitemap, config=None):
    """生成站点结构快照。

    兼容原有字段（``links`` 计数、``depth``、``set``），并增加栏目指纹、
    目录树和按栏目的链接索引。
    """
    cfg = _merge_config(config)
    sitemap = sitemap or {}
    raw_links = list(sitemap.get("links") or [])
    level = cfg["section_level"]

    grouped = {}
    tree = {}
    inferred_depth = 0
    # 结构指纹只看归一后的结构：重复链接和抓取顺序都不影响结果。
    for link in sorted(set(raw_links)):
        segments = _path_segments(link)
        depth = len(segments)
        inferred_depth = max(inferred_depth, depth)
        section = _section_name(segments, level)
        entry = grouped.setdefault(section, {"links": [], "depths": Counter()})
        entry["links"].append(link)
        entry["depths"][depth] += 1
        for node in _tree_nodes(segments, section):
            item = tree.setdefault(
                node,
                {"links": 0, "depth": _section_depth(node), "parent": _section_parent(node)},
            )
            item["links"] += 1

    unique_by_section = {name: entry["links"] for name, entry in grouped.items()}

    sections = {}
    for name in sorted(grouped):
        entry = grouped[name]
        depths = {str(depth): count for depth, count in sorted(entry["depths"].items())}
        fingerprint = {
            "section": name,
            "level": _section_depth(name),
            "parent": _section_parent(name),
            "links": len(entry["links"]),
            "depths": depths,
            "min_depth": min(entry["depths"]) if entry["depths"] else 0,
            "max_depth": max(entry["depths"]) if entry["depths"] else 0,
        }
        fingerprint["digest"] = _digest(
            {
                "version": FINGERPRINT_VERSION,
                "section": name,
                "links": fingerprint["links"],
                "depths": depths,
            }
        )
        sections[name] = fingerprint

    payload = {
        "version": FINGERPRINT_VERSION,
        "section_level": level,
        "sections": {
            name: {"links": item["links"], "depths": item["depths"]}
            for name, item in sections.items()
        },
    }
    digest = _digest(payload)

    declared_depth = sitemap.get("depth", 1)
    try:
        declared_depth = int(declared_depth)
    except (TypeError, ValueError):
        declared_depth = 1

    return {
        # 兼容旧接口的三个字段；depth 仍以传入的声明值为准。
        "links": len(raw_links),
        "depth": declared_depth,
        "set": set(raw_links),
        # 结构指纹。
        "total": len(raw_links),
        "inferred_depth": inferred_depth,
        "section_level": level,
        "sections": sections,
        "tree": tree,
        "links_by_section": unique_by_section,
        "fingerprint": {
            "digest": digest,
            "version": FINGERPRINT_VERSION,
            "section_level": level,
            "links": len(raw_links),
            "depth": declared_depth,
            "inferred_depth": inferred_depth,
            "sections": len(sections),
            "by_section": {name: item["digest"] for name, item in sections.items()},
        },
    }


def _new_section_alerts(old_sections, new_sections, links_by_section, cfg, skip=()):
    alerts = []
    for section in sorted(set(new_sections) - set(old_sections)):
        if section in skip:
            continue
        fingerprint = new_sections[section]
        if fingerprint["links"] < cfg["new_section_min_links"]:
            continue
        alerts.append(
            {
                "type": "new_section",
                "key": "new_section:%s" % section,
                "section": section,
                "scope": "section",
                "message": "新增栏目 %s（%d 条链接，深度 %s）"
                % (section, fingerprint["links"], sorted(fingerprint["depths"])),
                "old": None,
                "new": {
                    "section": section,
                    "links": fingerprint["links"],
                    "depths": fingerprint["depths"],
                    "digest": fingerprint["digest"],
                },
                "samples": {
                    "old": [],
                    "new": _sample(links_by_section.get(section, []), cfg["sample_size"]),
                },
            }
        )
    return alerts


def _level_change_alerts(old, new, old_set, new_set, cfg):
    alerts = []
    old_sections = old.get("sections") or {}
    new_sections = new.get("sections") or {}

    # 1) 同一栏目名（路径末段）换了父级或深度：栏目被重新挂载。
    old_tree = old.get("tree") or {}
    new_tree = new.get("tree") or {}
    old_by_leaf = defaultdict(list)
    new_by_leaf = defaultdict(list)
    minimum = cfg["level_change_min_links"]
    for path, node in old_tree.items():
        if node["links"] >= minimum:
            old_by_leaf[path.rsplit("/", 1)[-1]].append(path)
    for path, node in new_tree.items():
        if node["links"] >= minimum:
            new_by_leaf[path.rsplit("/", 1)[-1]].append(path)

    moves = []
    for leaf in sorted(set(old_by_leaf) & set(new_by_leaf)):
        old_paths = [path for path in old_by_leaf[leaf] if path not in new_tree]
        new_paths = [path for path in new_by_leaf[leaf] if path not in old_tree]
        # 只有一一对应时才认定为“移动”，多义情况交给新增/删除告警。
        if len(old_paths) != 1 or len(new_paths) != 1:
            continue
        moves.append((old_paths[0], new_paths[0]))
    moves.sort(key=lambda pair: (_section_depth(pair[0]), pair[0], pair[1]))

    # 子节点随父节点一起移动，只报最浅的那次，避免逐层重复。
    reported = []
    for old_path, new_path in moves:
        if any(
            old_path != seen_old
            and old_path.startswith(seen_old + "/")
            and new_path.startswith(seen_new + "/")
            for seen_old, seen_new in reported
        ):
            continue
        reported.append((old_path, new_path))
        old_samples = _sample(
            [link for link in old_set if _link_under(link, old_path)], cfg["sample_size"]
        )
        new_samples = _sample(
            [link for link in new_set if _link_under(link, new_path)], cfg["sample_size"]
        )
        alerts.append(
            {
                "type": "level_change",
                "key": "level_change:%s->%s" % (old_path, new_path),
                "section": new_path,
                "scope": "section",
                "message": "栏目层级变化：%s（深度 %d）-> %s（深度 %d）"
                % (old_path, _section_depth(old_path), new_path, _section_depth(new_path)),
                "old": {
                    "section": old_path,
                    "depth": _section_depth(old_path),
                    "links": old_tree[old_path]["links"],
                },
                "new": {
                    "section": new_path,
                    "depth": _section_depth(new_path),
                    "links": new_tree[new_path]["links"],
                },
                "samples": {"old": old_samples, "new": new_samples},
            }
        )

    # 2) 栏目仍在，但最浅的深度层整体消失：内容被下移了一层。
    for section in sorted(set(old_sections) & set(new_sections)):
        old_fp = old_sections[section]
        new_fp = new_sections[section]
        old_depths = {int(depth): count for depth, count in old_fp["depths"].items()}
        new_depths = {int(depth): count for depth, count in new_fp["depths"].items()}
        if old_fp["min_depth"] == new_fp["min_depth"] and not set(old_depths) - set(new_depths):
            continue
        lost_levels = sorted(set(old_depths) - set(new_depths))
        affected = sum(old_depths[depth] for depth in lost_levels)
        if affected < minimum:
            continue
        old_samples = _sample(
            [
                link
                for link in old_set
                if _section_of(link, old.get("section_level", 1)) == section
                and len(_path_segments(link)) in lost_levels
            ],
            cfg["sample_size"],
        )
        new_samples = _sample(
            [
                link
                for link in new_set
                if _section_of(link, new.get("section_level", 1)) == section
            ],
            cfg["sample_size"],
        )
        alerts.append(
            {
                "type": "level_change",
                "key": "level_change:%s:depth-profile" % section,
                "section": section,
                "scope": "section",
                "message": "栏目 %s 层级变化：最浅深度 %d -> %d，消失深度 %s"
                % (section, old_fp["min_depth"], new_fp["min_depth"], lost_levels),
                "old": {
                    "section": section,
                    "depths": old_fp["depths"],
                    "min_depth": old_fp["min_depth"],
                    "links": old_fp["links"],
                },
                "new": {
                    "section": section,
                    "depths": new_fp["depths"],
                    "min_depth": new_fp["min_depth"],
                    "links": new_fp["links"],
                },
                "samples": {"old": old_samples, "new": new_samples},
            }
        )
    return alerts


def _bulk_removal_alerts(old_sections, new_sections, removed, removed_by_section, cfg):
    alerts = []
    threshold_ratio = cfg["bulk_removal_ratio"]
    threshold_count = cfg["bulk_removal_min"]
    old_total = sum(item["links"] for item in old_sections.values())
    new_total = sum(item["links"] for item in new_sections.values())
    removed_total = len(removed)
    # 单栏目的删除由下面的栏目级告警说明；站点级只在跨栏目时补充。
    if old_total and removed_total >= threshold_count and len(removed_by_section) > 1:
        ratio = removed_total / float(old_total)
        if ratio >= threshold_ratio:
            alerts.append(
                {
                    "type": "bulk_removal",
                    "key": "bulk_removal:site",
                    "section": None,
                    "scope": "site",
                    "message": "大量删除：%d/%d 条（%.0f%%）超过阈值 %.0f%%，涉及 %d 个栏目"
                    % (
                        removed_total,
                        old_total,
                        ratio * 100,
                        threshold_ratio * 100,
                        len(removed_by_section),
                    ),
                    "old": {
                        "links": old_total,
                        "removed": removed_total,
                        "ratio": round(ratio, 4),
                    },
                    "new": {"links": new_total},
                    "samples": {
                        "old": _sample(removed, cfg["sample_size"]),
                        "new": [],
                    },
                }
            )

    for section in sorted(removed_by_section):
        old_count = (old_sections.get(section) or {}).get("links", 0)
        removed_count = len(removed_by_section[section])
        if not old_count or removed_count < threshold_count:
            continue
        ratio = removed_count / float(old_count)
        if ratio < threshold_ratio:
            continue
        alerts.append(
            {
                "type": "bulk_removal",
                "key": "bulk_removal:section:%s" % section,
                "section": section,
                "scope": "section",
                "message": "栏目 %s 大量删除：%d/%d 条（%.0f%%）"
                % (section, removed_count, old_count, ratio * 100),
                "old": {
                    "section": section,
                    "links": old_count,
                    "removed": removed_count,
                    "ratio": round(ratio, 4),
                },
                "new": {"links": (new_sections.get(section) or {}).get("links", 0)},
                "samples": {
                    "old": _sample(removed_by_section[section], cfg["sample_size"]),
                    "new": [],
                },
            }
        )
    return alerts


def _apply_escalation(alerts, cfg, state):
    """按告警身份累计连续批次；缺席即清零。"""
    if state is None:
        state = {}
    streaks = state.setdefault("streaks", {})
    state["batches"] = state.get("batches", 0) + 1
    current = {alert["key"] for alert in alerts}
    for key in list(streaks):
        if key not in current:
            del streaks[key]
    for alert in alerts:
        streaks[alert["key"]] = streaks.get(alert["key"], 0) + 1
        alert["streak"] = streaks[alert["key"]]
        alert["severity"] = "alert" if alert["streak"] >= cfg["escalate_after"] else "warning"
    return state


def _normalize_snapshot(snap, cfg):
    """兼容旧版快照：只有 links/depth/set 时按当前配置补出结构字段。"""
    if not snap or "sections" in snap:
        return snap
    legacy_links = sorted(snap.get("set") or [])
    if not legacy_links:
        return snap
    return snapshot(
        {"links": legacy_links, "depth": snap.get("depth", 1)},
        config={"section_level": snap.get("section_level", cfg["section_level"])},
    )


def diff(old, new, config=None, state=None):
    """比对两个 :func:`snapshot` 结果。

    返回值包含：

    - ``fingerprint``：新旧指纹及逐栏目指纹；
    - ``changes`` / ``added`` / ``removed``：链接差异，``by_section`` 带
      新旧栏目对比；
    - ``samples``：按栏目抽样的变更链接；
    - ``alerts``：新增栏目、层级变化、大量删除三类告警，带样本和
      新旧对比；``escalated`` 是其中已连续多批升级的部分。
    """
    cfg = _merge_config(config)
    old = _normalize_snapshot(old or {}, cfg)
    new = _normalize_snapshot(new or {}, cfg)
    old_level = old.get("section_level")
    new_level = new.get("section_level")
    if old_level is not None and new_level is not None and old_level != new_level:
        raise ValueError(
            "新旧快照的 section_level 不一致: %s != %s" % (old_level, new_level)
        )
    old_set = old.get("set") or set()
    new_set = new.get("set") or set()
    old_sections = old.get("sections") or {}
    new_sections = new.get("sections") or {}
    old_links_by_section = old.get("links_by_section") or {}
    new_links_by_section = new.get("links_by_section") or {}
    level = int(new.get("section_level", old.get("section_level", cfg["section_level"])))
    sample_size = cfg["sample_size"]

    added = sorted(new_set - old_set)
    removed = sorted(old_set - new_set)
    added_by_section = _group_by_section(added, level)
    removed_by_section = _group_by_section(removed, level)

    by_section = {}
    samples = {}
    candidates = set(old_sections) | set(new_sections) | set(added_by_section) | set(removed_by_section)
    for section in sorted(candidates):
        old_fp = old_sections.get(section)
        new_fp = new_sections.get(section)
        added_links = added_by_section.get(section, [])
        removed_links = removed_by_section.get(section, [])
        fingerprint_changed = (old_fp or {}).get("digest") != (new_fp or {}).get("digest")
        if not added_links and not removed_links and not fingerprint_changed:
            continue
        section_samples = {
            "added": _sample(added_links, sample_size),
            "removed": _sample(removed_links, sample_size),
        }
        by_section[section] = {
            "old": old_fp,
            "new": new_fp,
            "added": len(added_links),
            "removed": len(removed_links),
            "samples": section_samples,
        }
        samples[section] = section_samples

    alerts = []
    alerts.extend(_level_change_alerts(old, new, old_set, new_set, cfg))
    moved_targets = {
        alert["new"]["section"]
        for alert in alerts
        if alert["type"] == "level_change" and alert["new"].get("section")
    }
    # 层级变化的目标栏目已经由移动告警说明，不再重复报“新增栏目”。
    alerts.extend(
        _new_section_alerts(
            old_sections, new_sections, new_links_by_section, cfg, skip=moved_targets
        )
    )
    alerts.extend(
        _bulk_removal_alerts(old_sections, new_sections, removed, removed_by_section, cfg)
    )
    alerts.sort(key=lambda item: (_ALERT_ORDER.get(item["type"], 99), item["section"] or "", item["key"]))
    _apply_escalation(alerts, cfg, state)
    escalated = [alert for alert in alerts if alert["severity"] == "alert"]

    old_digest = (old.get("fingerprint") or {}).get("digest")
    new_digest = (new.get("fingerprint") or {}).get("digest")

    return {
        "added": added,
        "removed": removed,
        "fingerprint": {
            "old": old_digest,
            "new": new_digest,
            "changed": old_digest != new_digest,
            "sections": {"old": old_sections, "new": new_sections},
        },
        "changes": {
            "added": len(added),
            "removed": len(removed),
            "by_section": by_section,
        },
        "samples": samples,
        "alerts": alerts,
        "escalated": escalated,
        "summary": {
            "batch": (state or {}).get("batches", 1),
            "links": {"old": len(old_set), "new": len(new_set)},
            "added": len(added),
            "removed": len(removed),
            "sections": {
                "old": len(old_sections),
                "new": len(new_sections),
                "added": sorted(set(new_sections) - set(old_sections)),
                "removed": sorted(set(old_sections) - set(new_sections)),
            },
            "alerts": len(alerts),
            "escalated": len(escalated),
        },
    }


class Tracker:
    """跨批次累计连续告警状态的封装。"""

    def __init__(self, config=None):
        self.config = _merge_config(config)
        self.state = {"batches": 0, "streaks": {}}

    def diff(self, old, new):
        return diff(old, new, config=self.config, state=self.state)

    def reset(self):
        self.state = {"batches": 0, "streaks": {}}

    @property
    def batch(self):
        return self.state["batches"]
