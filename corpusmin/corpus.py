"""按覆盖贡献收敛模糊测试语料。

语料条目是一个 dict，至少带 ``id``；常用字段：

    {"id": "seed-01", "project": "pdf", "size": 120,
     "covers": ["pdf/parser.c:10-40", "pdf/xref.c:1-9"]}

``covers`` / ``coverage`` / ``regions`` 任选一个（也可同时给，取并集），
用来声明这份语料命中的代码区域。也接受 ``{文件: [行号, ...]}`` 形式。
没有声明覆盖字段的条目视为“覆盖未知”，一律保留；显式声明为空集
（``covers: []``）才表示确认没有覆盖贡献。

收敛规则：按覆盖贡献从大到小做确定性的贪心集合覆盖，只丢弃其覆盖
已被保留语料完全覆盖的条目。因此保留集合的覆盖与收敛前完全一致，
收敛后的集合能复跑出同样的覆盖。被丢弃的条目连同原始内容和覆盖区域
进入按项目的 archive，可用 ``restore`` 取回。每次 minimize / add /
restore 生成一个轮次，轮次里保留当时的覆盖快照，便于追溯。

项目之间互不影响：语料、archive、轮次都按 project 分开保存。
"""

import heapq

_DEFAULT_PROJECT = "default"
_COVER_KEYS = ("covers", "coverage", "regions")

# project -> {
#   "corpus": {id: entry}, "archive": {id: record},
#   "rounds": [...], "contributions": {id: [region]}, "selection": [id],
# }
_projects = {}
_round = 0


# ---------------------------------------------------------------- 条目规范化


def _project_name(item):
    project = item.get("project", _DEFAULT_PROJECT)
    if project is None or project == "":
        return _DEFAULT_PROJECT
    return project if isinstance(project, str) else str(project)


def _size(item):
    size = item.get("size", 0)
    if isinstance(size, bool) or not isinstance(size, (int, float)):
        return 0
    return size


def _collect_hits(out, filename, hits):
    """把 {文件: 行号} 映射展开成区域 id；命中值为假表示该行未覆盖。"""
    if hits is False or hits is None:
        return
    if isinstance(hits, dict):
        for line, hit in hits.items():
            if hit:
                out.add("%s:%s" % (filename, line))
    elif isinstance(hits, (list, tuple, set, frozenset)):
        for line in hits:
            out.add("%s:%s" % (filename, line))
    elif isinstance(hits, str):
        out.add("%s:%s" % (filename, hits))
    elif isinstance(hits, (int, float)) and not isinstance(hits, bool):
        out.add("%s:%s" % (filename, hits))
    else:
        out.add(filename)


def _as_regions(value):
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        if "regions" in value:
            return _as_regions(value["regions"])
        out = set()
        for filename, hits in value.items():
            _collect_hits(out, str(filename), hits)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return {item if isinstance(item, str) else str(item) for item in value}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {str(value)}
    raise ValueError(
        "coverage must be a region id, a list of region ids, "
        "or a file->lines mapping"
    )


def _normalize_covers(item):
    regions = set()
    found = False
    for key in _COVER_KEYS:
        if key in item:
            found = True
            try:
                regions |= _as_regions(item[key])
            except ValueError as exc:
                raise ValueError("corpus item %r: %s" % (item["id"], exc))
    return regions if found else None


def _make_entry(item, project):
    return {
        "id": item["id"],
        "project": project,
        "size": _size(item),
        "covers": _normalize_covers(item),
        # 保留一份顶层副本，调用方之后改原 dict 不影响已收敛的集合。
        "raw": dict(item),
    }


def _check(items, what="corpus"):
    if not isinstance(items, list):
        raise ValueError("%s must be a list" % what)
    seen = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("%s item must be a dict" % what)
        if "id" not in item:
            raise ValueError("%s item needs id" % what)
        project = _project_name(item)
        ids = seen.setdefault(project, set())
        if item["id"] in ids:
            raise ValueError(
                "duplicate corpus id %r in project %r" % (item["id"], project)
            )
        ids.add(item["id"])


def _group(items):
    groups = {}
    for item in items:
        project = _project_name(item)
        groups.setdefault(project, []).append(_make_entry(item, project))
    return groups


# ---------------------------------------------------------------- 项目状态


def _new_state():
    return {
        "corpus": {},
        "archive": {},
        "rounds": [],
        "contributions": {},
        "selection": [],
    }


def _state(project):
    return _projects.setdefault(project, _new_state())


def _sorted_ids(ids):
    return sorted(ids, key=str)


def _sources_of(entries):
    sources = {}
    for entry in entries:
        if entry["covers"] is None:
            continue
        for region in entry["covers"]:
            sources.setdefault(region, set()).add(entry["id"])
    return sources


def _solo_detail(sources):
    solo = [region for region in sources if len(sources[region]) == 1]
    sole = {region: next(iter(sources[region])) for region in solo}
    return _sorted_ids(solo), {region: sole[region] for region in _sorted_ids(solo)}


def _region_detail(before, after):
    detail = {}
    for region in _sorted_ids(set(before) | set(after)):
        before_sources = before.get(region, set())
        after_sources = after.get(region, set())
        detail[region] = {
            "covered_before": bool(before_sources),
            "covered_after": bool(after_sources),
            "before": len(before_sources),
            "after": len(after_sources),
            "solo_before": len(before_sources) == 1,
            "solo_after": len(after_sources) == 1,
            "solo": len(after_sources) == 1,
            "sources_before": _sorted_ids(before_sources),
            "kept_by": _sorted_ids(after_sources),
        }
    return detail


def _round_record(project, round_no, action, before_sources, after_sources, **extra):
    """轮次快照：按区域记录覆盖计数和唯一支撑关系，不存整份语料。"""
    solo_after, sole_after = _solo_detail(after_sources)
    record = {
        "round": round_no,
        "project": project,
        "action": action,
        "coverage_before": len(before_sources),
        "coverage_after": len(after_sources),
        "coverage_delta": len(after_sources) - len(before_sources),
        # 冗余度 = 所有区域被多少份语料覆盖的总和，收敛主要降这个数。
        "source_refs_before": sum(len(v) for v in before_sources.values()),
        "source_refs_after": sum(len(v) for v in after_sources.values()),
        "region_counts": {
            region: len(after_sources[region]) for region in _sorted_ids(after_sources)
        },
        "region_counts_before": {
            region: len(before_sources[region]) for region in _sorted_ids(before_sources)
        },
        "regions_added": _sorted_ids(set(after_sources) - set(before_sources)),
        "regions_lost": _sorted_ids(set(before_sources) - set(after_sources)),
        "solo_regions": solo_after,
        "sole_source": sole_after,
    }
    record.update(extra)
    return record


# ---------------------------------------------------------------- 报告


def _empty_project_report(project):
    return {
        "project": project,
        "total": 0,
        "kept_count": 0,
        "dropped_count": 0,
        "kept_ids": [],
        "dropped_ids": [],
        "size": 0,
        "regions": 0,
        "coverage": 0,
        "coverage_before": 0,
        "coverage_after": 0,
        "coverage_change": {
            "before": 0,
            "after": 0,
            "delta": 0,
            "lost_regions": [],
        },
        "last_minimize_round": None,
        "coverage_by_region": {},
        "solo_regions": [],
        "sole_source": {},
        "unknown_coverage": [],
        "contributions": {},
        "archive": {},
        "rounds": [],
        "latest_round": None,
    }


def _project_report(project):
    state = _projects.get(project)
    if state is None:
        return _empty_project_report(project)
    entries = list(state["corpus"].values())
    sources = _sources_of(entries)
    solo_regions, sole_source = _solo_detail(sources)
    archive = {}
    for item_id in _sorted_ids(state["archive"]):
        record = state["archive"][item_id]
        archive[item_id] = {
            "id": record["id"],
            "project": record["project"],
            "round": record["round"],
            "reason": record["reason"],
            "covers": list(record["covers"]),
            "covered_by": list(record["covered_by"]),
            "duplicate_of": record["duplicate_of"],
            "coverage_round": record["coverage_round"],
        }
    rounds = list(state["rounds"])
    last_minimize = None
    for record in rounds:
        if record["action"] == "minimize":
            last_minimize = record
    now = len(sources)
    before = last_minimize["coverage_before"] if last_minimize else now
    return {
        "project": project,
        "total": len(entries),
        "kept_count": len(entries),
        "dropped_count": len(state["archive"]),
        "kept_ids": [entry["id"] for entry in entries],
        "dropped_ids": _sorted_ids(state["archive"]),
        "size": sum(entry["size"] for entry in entries),
        "regions": now,
        "coverage": now,
        "coverage_before": before,
        "coverage_after": now,
        "coverage_change": {
            "before": before,
            "after": now,
            "delta": now - before,
            "lost_regions": (
                last_minimize["regions_lost"] if last_minimize else []
            ),
        },
        "last_minimize_round": last_minimize["round"] if last_minimize else None,
        "coverage_by_region": {
            region: {
                "sources": _sorted_ids(sources[region]),
                "count": len(sources[region]),
                "solo": len(sources[region]) == 1,
            }
            for region in _sorted_ids(sources)
        },
        "solo_regions": solo_regions,
        "sole_source": sole_source,
        "unknown_coverage": [entry["id"] for entry in entries if entry["covers"] is None],
        "contributions": dict(state["contributions"]),
        "archive": archive,
        "rounds": rounds,
        "latest_round": rounds[-1]["round"] if rounds else None,
    }


def _empty_result():
    return {
        "project": None,
        "kept": [],
        "dropped": [],
        "kept_count": 0,
        "dropped_count": 0,
        "coverage_before": 0,
        "coverage_after": 0,
        "coverage_delta": 0,
        "coverage_change": {
            "before": 0,
            "after": 0,
            "delta": 0,
            "lost_regions": [],
        },
        "coverage_preserved": True,
        "coverage_by_region": {},
        "solo_regions": [],
        "solo_regions_before": [],
        "sole_source": {},
        "critical": [],
        "critical_coverage": {},
        "contributions": {},
        "unknown_coverage": [],
        "selection": [],
        "archive": {},
        "projects": {},
        "size_before": 0,
        "size_after": 0,
        "source_refs_before": 0,
        "source_refs_after": 0,
        "round": None,
    }


def _combine(per_project, round_no):
    """把各项目的结果合成一份总览；单项目时把明细提升到顶层。

    顶层 id 保持语料原本的写法；不同项目出现同 id 时，archive 的键写成
    ``项目/id`` 以避免互相覆盖。各项目明细保存在 ``projects`` 下。
    """
    names = list(per_project)

    id_counts = {}
    for result in per_project.values():
        ids = set(result["kept"])
        ids.update(result["dropped"])
        ids.update(result["unknown_coverage"])
        for item_id in ids:
            id_counts[item_id] = id_counts.get(item_id, 0) + 1

    def _label(project, item_id):
        return item_id if id_counts.get(item_id, 0) <= 1 else "%s/%s" % (project, item_id)

    kept, dropped, unknown, selection = [], [], [], []
    archive, contributions, critical_coverage = {}, {}, {}
    coverage_before = coverage_after = size_before = size_after = 0
    refs_before = refs_after = 0
    lost_regions = []
    solo_before, solo_after, critical = [], [], []
    for name in names:
        result = per_project[name]
        kept.extend(_label(name, item_id) for item_id in result["kept"])
        dropped.extend(_label(name, item_id) for item_id in result["dropped"])
        unknown.extend(_label(name, item_id) for item_id in result["unknown_coverage"])
        selection.extend(_label(name, item_id) for item_id in result["selection"])
        for item_id, record in result["archive"].items():
            archive[_label(name, item_id)] = record
        for item_id, regions in result["contributions"].items():
            contributions[_label(name, item_id)] = regions
        for item_id, regions in result["critical_coverage"].items():
            critical_coverage[_label(name, item_id)] = regions
        coverage_before += result["coverage_before"]
        coverage_after += result["coverage_after"]
        size_before += result["size_before"]
        size_after += result["size_after"]
        refs_before += result["source_refs_before"]
        refs_after += result["source_refs_after"]
        solo_before.extend(result["solo_regions_before"])
        solo_after.extend(result["solo_regions"])
        critical.extend(_label(name, item_id) for item_id in result["critical"])
        lost_regions.extend(result["coverage_change"]["lost_regions"])

    result = {
        "project": names[0] if len(names) == 1 else None,
        "kept": kept,
        "dropped": dropped,
        "kept_count": len(kept),
        "dropped_count": len(dropped),
        "coverage_before": coverage_before,
        "coverage_after": coverage_after,
        "coverage_delta": coverage_after - coverage_before,
        "coverage_change": {
            "before": coverage_before,
            "after": coverage_after,
            "delta": coverage_after - coverage_before,
            "lost_regions": _sorted_ids(set(lost_regions)),
        },
        "coverage_preserved": all(
            result["coverage_preserved"] for result in per_project.values()
        ),
        "coverage_by_region": {},
        "solo_regions": _sorted_ids(set(solo_after)),
        "solo_regions_before": _sorted_ids(set(solo_before)),
        "sole_source": {},
        "critical": _sorted_ids(set(critical)),
        "critical_coverage": critical_coverage,
        "contributions": contributions,
        "unknown_coverage": unknown,
        "selection": selection,
        "archive": archive,
        "projects": per_project,
        "size_before": size_before,
        "size_after": size_after,
        "source_refs_before": refs_before,
        "source_refs_after": refs_after,
        "round": round_no,
    }
    if len(names) == 1:
        detail = per_project[names[0]]
        for key in (
            "coverage_by_region",
            "solo_regions",
            "solo_regions_before",
            "sole_source",
            "critical",
            "critical_coverage",
            "unknown_coverage",
            "selection",
            "size_before",
            "size_after",
            "source_refs_before",
            "source_refs_after",
        ):
            result[key] = detail[key]
    return result


# ---------------------------------------------------------------- minimize


def _greedy_select(entries):
    """确定性的贪心集合覆盖。

    每步挑“能新增覆盖最多”的语料；同分时体积小的、id 小的优先。
    用区域反查表 + 惰性更新的堆，避免几十万份语料时的 O(n^2)。
    返回 (selection, contributions)：selection 是挑选顺序，
    contributions[id] 是它被选中时新增的区域。
    """
    covers_of = {entry["id"]: entry["covers"] for entry in entries}
    sizes = {entry["id"]: entry["size"] for entry in entries}

    # 稳定序号作为最后一级比较键：id 可能是 int 也可能是 str，不能直接比。
    ordered = sorted(covers_of, key=lambda entry_id: (str(entry_id), type(entry_id).__name__))
    rank = {entry_id: index for index, entry_id in enumerate(ordered)}

    holders = {}
    for entry_id, covers in covers_of.items():
        for region in covers:
            holders.setdefault(region, []).append(entry_id)

    # 堆项是 (-增益, 体积, 稳定序号)；增益只减不增，弹出时与当前值核对，
    # 不一致说明这是过期项，丢掉即可（新值已单独入堆）。
    heap = [
        (-len(covers_of[entry_id]), sizes[entry_id], rank[entry_id], entry_id)
        for entry_id in covers_of
    ]
    heapq.heapify(heap)

    uncovered = set(holders)
    selection = []
    contributions = {}
    gain = {entry_id: len(covers) for entry_id, covers in covers_of.items()}

    while heap:
        neg_gain, _, _, entry_id = heapq.heappop(heap)
        current = gain[entry_id]
        if current <= 0:
            continue
        if -neg_gain != current:
            # 过期项：按当前增益重新入堆。
            heapq.heappush(heap, (-current, sizes[entry_id], rank[entry_id], entry_id))
            continue
        added = covers_of[entry_id] & uncovered
        if not added:
            gain[entry_id] = 0
            continue
        selection.append(entry_id)
        contributions[entry_id] = _sorted_ids(added)
        uncovered -= added
        gain[entry_id] = 0
        # 被这次选择覆盖掉的区域，其它条目的潜在增益同步下降。
        for region in added:
            for other in holders[region]:
                if other == entry_id or gain[other] <= 0:
                    continue
                gain[other] -= 1
                if gain[other] > 0:
                    heapq.heappush(
                        heap, (-gain[other], sizes[other], rank[other], other)
                    )
        if not uncovered:
            break
    return selection, contributions


def _minimize_project(project, entries, round_no):
    state = _state(project)
    incoming = {entry["id"] for entry in entries}

    # 本次重新提交的语料重新进入评估；之前残留的同 id 记录不再算归档。
    for entry_id in incoming:
        state["archive"].pop(entry_id, None)

    # 上次保留、这次没提交的语料移入 archive（可 restore 取回），而不是静默消失。
    stale = [
        entry
        for entry_id, entry in state["corpus"].items()
        if entry_id not in incoming
    ]
    for entry in stale:
        state["archive"][entry["id"]] = {
            "id": entry["id"],
            "project": project,
            "round": round_no,
            "reason": "superseded",
            "covers": _sorted_ids(entry["covers"] or ()),
            "covered_by": [],
            "duplicate_of": None,
            "coverage_round": round_no,
            "entry": entry["raw"],
        }

    before_sources = _sources_of(entries)
    coverage_before = set(before_sources)
    solo_before, _ = _solo_detail(before_sources)

    critical_coverage = {}
    for entry in entries:
        regions = [
            region
            for region in entry["covers"] or ()
            if len(before_sources.get(region, ())) == 1
        ]
        if regions:
            critical_coverage[entry["id"]] = _sorted_ids(regions)

    # 确定性的贪心集合覆盖：每步挑新增覆盖最多的语料；同增益时体积小、
    # id 小的优先。只保留真正带来新增覆盖的条目。
    selection, contributions = _greedy_select(
        [entry for entry in entries if entry["covers"]]
    )
    kept_ids = set(selection)

    unknown = []
    for entry in entries:
        if entry["covers"] is None:
            kept_ids.add(entry["id"])
            contributions[entry["id"]] = []
            unknown.append(entry["id"])

    kept_entries = [entry for entry in entries if entry["id"] in kept_ids]
    after_sources = _sources_of(kept_entries)
    coverage_after = set(after_sources)
    solo_after, sole_source = _solo_detail(after_sources)

    kept = [entry["id"] for entry in kept_entries]
    # 完全重复（声明了同一组区域）的保留语料，方便解释丢弃原因。
    exact_cover = {}
    for entry in kept_entries:
        if entry["covers"]:
            exact_cover.setdefault(frozenset(entry["covers"]), entry["id"])
    archived_this_round = {}
    dropped = []
    for entry in entries:
        if entry["id"] in kept_ids:
            continue
        entry_id = entry["id"]
        covers = set(entry["covers"] or ())
        covered_by = set()
        for region in covers:
            covered_by.update(after_sources.get(region, ()))
        duplicate_of = exact_cover.get(frozenset(covers)) if covers else None
        reason = "no_coverage" if not covers else "fully_covered"
        record = {
            "id": entry_id,
            "project": project,
            "round": round_no,
            "reason": reason,
            "covers": _sorted_ids(covers),
            "covered_by": _sorted_ids(covered_by),
            "duplicate_of": duplicate_of,
            # 覆盖快照留在轮次里，这里只指向它，避免为每份语料重复存整张覆盖表。
            "coverage_round": round_no,
            "entry": entry["raw"],
        }
        state["archive"][entry_id] = record
        archived_this_round[entry_id] = record
        dropped.append(entry_id)

    state["corpus"] = {entry["id"]: entry for entry in kept_entries}
    state["contributions"] = contributions
    state["selection"] = selection
    state["rounds"].append(
        _round_record(
            project,
            round_no,
            "minimize",
            before_sources,
            after_sources,
            kept=kept,
            dropped=dropped,
            superseded=[entry["id"] for entry in stale],
            kept_count=len(kept),
            dropped_count=len(dropped),
            critical=_sorted_ids(critical_coverage),
        )
    )

    return {
        "project": project,
        "kept": kept,
        "dropped": dropped,
        "kept_count": len(kept),
        "dropped_count": len(dropped),
        "coverage_before": len(coverage_before),
        "coverage_after": len(coverage_after),
        "coverage_delta": len(coverage_after) - len(coverage_before),
        "source_refs_before": sum(len(v) for v in before_sources.values()),
        "source_refs_after": sum(len(v) for v in after_sources.values()),
        "coverage_change": {
            "before": len(coverage_before),
            "after": len(coverage_after),
            "delta": len(coverage_after) - len(coverage_before),
            "lost_regions": _sorted_ids(coverage_before - coverage_after),
        },
        # 收敛的硬约束：保留集合能复跑出同样的覆盖。
        "coverage_preserved": not (coverage_before - coverage_after),
        "coverage_by_region": _region_detail(before_sources, after_sources),
        "solo_regions": solo_after,
        "solo_regions_before": solo_before,
        "sole_source": sole_source,
        "critical": _sorted_ids(critical_coverage),
        "critical_coverage": critical_coverage,
        "contributions": contributions,
        "unknown_coverage": unknown,
        "selection": selection,
        "size_before": sum(entry["size"] for entry in entries),
        "size_after": sum(entry["size"] for entry in kept_entries),
        "archive": archived_this_round,
        "round": round_no,
    }


def minimize(corpus):
    """按覆盖贡献收敛语料。

    返回保留/丢弃数量、覆盖变化、按区域的前后对比，以及被丢弃语料
    去往哪个项目的 archive。传入多个项目时 ``projects`` 保存各项目明细。
    """
    global _round
    _check(corpus)
    groups = _group(corpus)
    if not groups:
        return _empty_result()
    _round += 1
    per_project = {
        project: _minimize_project(project, entries, _round)
        for project, entries in groups.items()
    }
    return _combine(per_project, _round)


# ---------------------------------------------------------------- add


def add(items):
    """把新语料加入当前集合，并记录每份新语料新增的覆盖。"""
    global _round
    _check(items, "items")
    groups = _group(items)
    if not groups:
        return {
            "added": 0,
            "total": sum(len(state["corpus"]) for state in _projects.values()),
            "new_regions": 0,
            "new_ids": [],
            "duplicate_ids": [],
            "unknown_coverage": [],
            "projects": {},
            "round": None,
        }
    _round += 1
    per_project = {}
    all_new = set()
    all_new_ids, all_duplicates, all_unknown = [], [], []
    for project, entries in groups.items():
        state = _state(project)
        before_sources = _sources_of(list(state["corpus"].values()))
        before = set(before_sources)
        covered = set(before)
        new_ids, duplicates, unknown = [], [], []
        contributions = {}
        for entry in entries:
            state["archive"].pop(entry["id"], None)
            state["corpus"][entry["id"]] = entry
            if entry["covers"] is None:
                contributions[entry["id"]] = []
                unknown.append(entry["id"])
                continue
            added = entry["covers"] - covered
            contributions[entry["id"]] = _sorted_ids(added)
            covered |= entry["covers"]
            if added:
                new_ids.append(entry["id"])
            else:
                duplicates.append(entry["id"])
        state["contributions"].update(contributions)
        after_sources = _sources_of(list(state["corpus"].values()))
        after = set(after_sources)
        state["rounds"].append(
            _round_record(
                project,
                _round,
                "add",
                before_sources,
                after_sources,
                added=[entry["id"] for entry in entries],
                new_ids=new_ids,
                duplicate_ids=duplicates,
            )
        )
        per_project[project] = {
            "added": len(entries),
            "new_regions": _sorted_ids(after - before),
            "new_ids": new_ids,
            "duplicate_ids": duplicates,
            "unknown_coverage": unknown,
            "coverage_before": len(before),
            "coverage_after": len(after),
            "contributions": contributions,
        }
        all_new |= after - before
        all_new_ids.extend(new_ids)
        all_duplicates.extend(duplicates)
        all_unknown.extend(unknown)
    return {
        "added": len(items),
        "total": sum(len(state["corpus"]) for state in _projects.values()),
        "new_regions": len(all_new),
        "new_ids": all_new_ids,
        "duplicate_ids": all_duplicates,
        "unknown_coverage": all_unknown,
        "projects": per_project,
        "round": _round,
    }


# ---------------------------------------------------------------- restore


def restore(ids, project=None):
    """从 archive 取回被丢弃的语料，连同它声明过的覆盖一起恢复。"""
    global _round
    if isinstance(ids, str):
        ids = [ids]
    elif not isinstance(ids, (list, tuple, set, frozenset)):
        raise ValueError("ids must be an id or a list of ids")
    ids = list(ids)

    wanted = None
    if project is not None:
        wanted = project if isinstance(project, str) else str(project)

    def _alive(item_id):
        if wanted is not None:
            state = _projects.get(wanted)
            return bool(state and item_id in state["corpus"])
        return any(item_id in state["corpus"] for state in _projects.values())

    selected = []  # (project, id, record)
    missing, ambiguous, already_present = [], [], []
    for item_id in ids:
        # minimize() 的总览在跨项目重名时给出 ``项目/id`` 形式的标签，
        # 这种标签在这里也认，保证从结果里复制出来的 id 能直接取回。
        label_project, label_id = None, item_id
        if isinstance(item_id, str) and "/" in item_id:
            prefix, _, rest = item_id.partition("/")
            candidate = _projects.get(prefix)
            if candidate is not None and rest in candidate["archive"]:
                label_project, label_id = prefix, rest

        if label_project is not None:
            selected.append(
                (label_project, label_id, _projects[label_project]["archive"][label_id])
            )
            continue

        matches = []
        if wanted is not None:
            state = _projects.get(wanted)
            if state and item_id in state["archive"]:
                matches.append((wanted, state["archive"][item_id]))
        else:
            for name, state in _projects.items():
                if item_id in state["archive"]:
                    matches.append((name, state["archive"][item_id]))
        if len(matches) == 1:
            selected.append((matches[0][0], item_id, matches[0][1]))
        elif len(matches) > 1:
            ambiguous.append(item_id)
        elif _alive(item_id):
            already_present.append(item_id)
        else:
            missing.append(item_id)

    result = {
        "project": wanted,
        "restored": [],
        "missing": missing,
        "ambiguous": ambiguous,
        "already_present": already_present,
        "coverage_before": 0,
        "coverage_after": 0,
        "coverage_delta": 0,
        "projects": {},
        "round": None,
    }
    if not selected:
        if not wanted and len(ids) == 1:
            result["project"] = None
        return result

    _round += 1
    by_project = {}
    for name, item_id, record in selected:
        by_project.setdefault(name, []).append((item_id, record))

    total_before = total_after = 0
    restored_ids = []
    for name, pairs in by_project.items():
        state = _state(name)
        before_sources = _sources_of(list(state["corpus"].values()))
        before = set(before_sources)  # 取回前的覆盖，用于对比
        covered = set(before)
        for item_id, record in pairs:
            entry = _make_entry(record["entry"], name)
            entry["id"] = item_id  # archive 记录以原 id 恢复
            # 恢复的是它原有的覆盖声明；它相对当前集合新增的区域单独记下。
            entry["covers"] = set(record["covers"])
            state["corpus"][item_id] = entry
            state["archive"].pop(item_id, None)
            state["contributions"][item_id] = _sorted_ids(entry["covers"] - covered)
            covered |= entry["covers"]
            restored_ids.append(item_id)
        after_sources = _sources_of(list(state["corpus"].values()))
        after = set(after_sources)
        state["rounds"].append(
            _round_record(
                name,
                _round,
                "restore",
                before_sources,
                after_sources,
                restored=[item_id for item_id, _ in pairs],
            )
        )
        per_project = {
            "restored": [item_id for item_id, _ in pairs],
            "coverage_before": len(before),
            "coverage_after": len(after),
            "coverage_delta": len(after) - len(before),
        }
        result["projects"][name] = per_project
        total_before += len(before)
        total_after += len(after)

    result.update(
        {
            "project": list(by_project)[0] if len(by_project) == 1 else None,
            "restored": restored_ids,
            "coverage_before": total_before,
            "coverage_after": total_after,
            "coverage_delta": total_after - total_before,
            "round": _round,
        }
    )
    return result


# ---------------------------------------------------------------- report


def report(project=None):
    """按项目查看收敛结果；覆盖数据按轮次保留在 ``rounds`` 里。

    ``report()`` 返回所有项目的总览，``report("pdf")`` 只返回 pdf 的明细。
    """
    if project is not None:
        return _project_report(project if isinstance(project, str) else str(project))
    projects = {name: _project_report(name) for name in _projects}
    rounds = []
    for detail in projects.values():
        rounds.extend(detail["rounds"])
    rounds.sort(key=lambda record: (record["round"], str(record["project"])))
    total = sum(detail["kept_count"] for detail in projects.values())
    dropped = sum(detail["dropped_count"] for detail in projects.values())
    return {
        "projects": projects,
        "total": total,
        "kept": total,
        "kept_count": total,
        "dropped": dropped,
        "dropped_count": dropped,
        "size": sum(detail["size"] for detail in projects.values()),
        "regions": sum(detail["regions"] for detail in projects.values()),
        "coverage": sum(detail["regions"] for detail in projects.values()),
        "rounds": rounds,
    }


def reset():
    """清空所有项目状态，主要供测试和本地演示复用。"""
    global _round
    _projects.clear()
    _round = 0
