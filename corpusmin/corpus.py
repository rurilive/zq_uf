"""按覆盖贡献收敛模糊测试语料。

每条语料是一个 dict：至少带 id，可选 project（默认 "default"）、size、
coverage（该语料覆盖的代码区域列表，如 ["parser:header", "parser:body"]）。

minimize 按项目分别做贪心集合覆盖：只保留能带来新增覆盖的语料，
没有新增覆盖的进 archive（连同它当年带来的覆盖一起留存，可复现）。
report 按项目给出收敛结果和区域级覆盖对比，标出只靠一份语料撑着的区域。
restore 把丢错的语料连同覆盖一起取回。每轮 minimize 的覆盖数据都留在
rounds 里，便于追溯。
"""

_corpus = []       # 当前保留的语料
_archive = {}      # archive_key -> {"item", "project", "round", "coverage"}
_rounds = []       # 每轮 minimize 的覆盖记录，按轮次追加
_round_counter = 0


def _reset():
    """清空全部状态，供测试使用。"""
    global _corpus, _archive, _rounds, _round_counter
    _corpus = []
    _archive = {}
    _rounds = []
    _round_counter = 0


def _check(corpus):
    if not isinstance(corpus, list):
        raise ValueError("corpus must be a list")
    for item in corpus:
        if not isinstance(item, dict):
            raise ValueError("corpus item must be a dict")
        if "id" not in item:
            raise ValueError("corpus item needs id")
        coverage = item.get("coverage")
        if coverage is not None and not isinstance(coverage, list):
            raise ValueError("coverage must be a list of regions")


def _project_of(item):
    return item.get("project", "default")


def _coverage_of(item):
    """返回区域的 set；没有 coverage 字段返回 None（贡献未知，不敢丢）。"""
    coverage = item.get("coverage")
    if coverage is None:
        return None
    return set(coverage)


def _minimize_project(items):
    """贪心集合覆盖：按覆盖量从大到小，留下能带来新增区域的。"""
    unknown = [it for it in items if _coverage_of(it) is None]
    known = [it for it in items if _coverage_of(it) is not None]
    known.sort(key=lambda it: (-len(_coverage_of(it)), str(it["id"])))

    kept = list(unknown)
    dropped = []
    covered = set()
    for item in known:
        coverage = _coverage_of(item)
        if coverage - covered:
            covered |= coverage
            kept.append(item)
        else:
            dropped.append(item)
    return kept, dropped


def minimize(corpus):
    """按项目收敛语料，返回保留/丢弃数量、覆盖变化和丢弃语料的去向。"""
    global _corpus, _round_counter
    _check(corpus)
    _round_counter += 1
    round_id = _round_counter

    by_project = {}
    for item in corpus:
        by_project.setdefault(_project_of(item), []).append(item)

    kept, dropped, archive_map = [], [], {}
    projects_detail = {}
    for project in sorted(by_project):
        items = by_project[project]
        before = set()
        for item in items:
            coverage = _coverage_of(item)
            if coverage is not None:
                before |= coverage

        p_kept, p_dropped = _minimize_project(items)

        after = set()
        for item in p_kept:
            coverage = _coverage_of(item)
            if coverage is not None:
                after |= coverage

        for item in p_dropped:
            key = "%s/r%d/%s" % (project, round_id, item["id"])
            _archive[key] = {
                "item": dict(item),
                "project": project,
                "round": round_id,
                "coverage": sorted(_coverage_of(item) or set()),
            }
            archive_map[item["id"]] = key

        kept.extend(p_kept)
        dropped.extend(p_dropped)
        projects_detail[project] = {
            "kept": [it["id"] for it in p_kept],
            "dropped": [it["id"] for it in p_dropped],
            "coverage_before": len(before),
            "coverage_after": len(after),
        }
        _rounds.append({
            "round": round_id,
            "project": project,
            "coverage_before": sorted(before),
            "coverage_after": sorted(after),
            "kept": [it["id"] for it in p_kept],
            "dropped": [it["id"] for it in p_dropped],
        })

    _corpus = list(kept)
    order = {id(item): i for i, item in enumerate(corpus)}
    kept.sort(key=lambda it: order[id(it)])
    dropped.sort(key=lambda it: order[id(it)])
    coverage_before = sum(d["coverage_before"] for d in projects_detail.values())
    coverage_after = sum(d["coverage_after"] for d in projects_detail.values())
    return {
        "round": round_id,
        "kept": [it["id"] for it in kept],
        "dropped": [it["id"] for it in dropped],
        "kept_count": len(kept),
        "dropped_count": len(dropped),
        "coverage_before": coverage_before,
        "coverage_after": coverage_after,
        "coverage_delta": coverage_after - coverage_before,
        "archive": archive_map,
        "projects": projects_detail,
    }


def add(items):
    """新语料进集合，并统计它相对所在项目现有覆盖带来了多少新增区域。"""
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            raise ValueError("item needs id")

    covered_by_project = {}
    for item in _corpus:
        coverage = _coverage_of(item)
        if coverage is not None:
            covered_by_project.setdefault(_project_of(item), set()).update(coverage)

    new_regions = 0
    for item in items:
        coverage = _coverage_of(item)
        if coverage is not None:
            project = _project_of(item)
            existing = covered_by_project.setdefault(project, set())
            new_regions += len(coverage - existing)
            existing |= coverage
    _corpus.extend(items)
    return {"added": len(items), "total": len(_corpus), "new_coverage": new_regions}


def restore(project, ids=None):
    """把 archive 里属于某项目的语料取回，连同它当年带来的覆盖一起恢复。"""
    restored, missing = [], []
    want = set(ids) if ids is not None else None
    for key in sorted(_archive):
        entry = _archive[key]
        if entry["project"] != project:
            continue
        item_id = entry["item"]["id"]
        if want is not None and item_id not in want:
            continue
        _corpus.append(dict(entry["item"]))
        restored.append(item_id)
        del _archive[key]
    if want is not None:
        missing = sorted(want - set(restored))
    coverage_restored = set()
    for item in _corpus:
        if item["id"] in restored:
            coverage_restored |= _coverage_of(item) or set()
    return {
        "project": project,
        "restored": restored,
        "missing": missing,
        "coverage_restored": len(coverage_restored),
    }


def _regions_for(project):
    """区域 -> 覆盖它的保留语料；archive 里的覆盖用于标出丢失的区域。"""
    active = {}
    for item in _corpus:
        if _project_of(item) != project:
            continue
        for region in _coverage_of(item) or set():
            active.setdefault(region, []).append(item["id"])
    archived = {}
    for entry in _archive.values():
        if entry["project"] != project:
            continue
        for region in entry["coverage"]:
            archived.setdefault(region, []).append(entry["item"]["id"])
    regions = {}
    for region in sorted(set(active) | set(archived)):
        holders = active.get(region, [])
        regions[region] = {
            "covered_by": holders,
            "solo": len(holders) == 1,
            "lost": not holders,
        }
    return regions


def report(project=None):
    """按项目查看收敛结果：份数、覆盖变化、区域对比和每轮历史。"""
    projects = sorted(
        {_project_of(item) for item in _corpus}
        | {entry["project"] for entry in _archive.values()}
        | {r["project"] for r in _rounds}
    )
    if project is not None:
        projects = [project] if project in projects else [project]

    body = {}
    for name in projects:
        kept_items = [it for it in _corpus if _project_of(it) == name]
        archived = [e for e in _archive.values() if e["project"] == name]
        rounds = [r for r in _rounds if r["project"] == name]
        regions = _regions_for(name)
        body[name] = {
            "kept": len(kept_items),
            "archived": len(archived),
            "size": sum(it.get("size", 0) for it in kept_items),
            "coverage": sum(1 for r in regions.values() if not r["lost"]),
            "coverage_before": len(rounds[-1]["coverage_before"]) if rounds else 0,
            "coverage_after": len(rounds[-1]["coverage_after"]) if rounds else 0,
            "solo_regions": sorted(r for r, d in regions.items() if d["solo"]),
            "lost_regions": sorted(r for r, d in regions.items() if d["lost"]),
            "regions": regions,
            "rounds": rounds,
        }
    return {
        "projects": body,
        "total": sum(d["kept"] for d in body.values()),
        "size": sum(d["size"] for d in body.values()),
        "regions": sum(d["coverage"] for d in body.values()),
    }
