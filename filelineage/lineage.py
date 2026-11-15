"""文件级血缘与影响分析，纯 Python 标准库。

节点是规范化后的文件路径，边由任务“读取一批文件、写入一批文件”派生：
每个 ``read -> write`` 对应一条 ``source -> target`` 的派生边。任务记录的
是任务级血缘，建立图时按 ``(source, target, batch)`` 归集成文件级边。

对外主接口保持 ``record(job)`` 与 ``impact(path)``，另提供上下游查询、
路径追踪和整图导出。
"""

from __future__ import annotations

import json
import posixpath
from collections import deque

DEFAULT_BATCH = "default"
DEFAULT_READ_MODE = "r"
DEFAULT_WRITE_MODE = "w"
_DIRECTIONS = {"downstream", "upstream"}
_RENAME_MODES = {"rename", "move", "mv"}

# 已记录的任务级血缘，键是任务 id。
_jobs: dict[str, dict] = {}
# 最近一次由任务级血缘重建出的文件级图。
_graph: dict = {}
_dirty = True


def _norm_path(value) -> str:
    """把路径规范成节点 id：统一分隔符并做 posix 规范化。"""
    if value is None:
        return ""
    path = str(value).strip().replace("\\", "/")
    if not path:
        return ""
    path = posixpath.normpath(path)
    return "" if path == "." else path


def _norm_batch(value) -> str:
    if value is None:
        return DEFAULT_BATCH
    value = str(value).strip()
    return value or DEFAULT_BATCH


def _norm_mode(value, default: str) -> str:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip()


def _norm_entry(raw, default_mode: str, job_batch: str) -> dict | None:
    """把一个读/写条目统一成 {path, mode, batch, from}。

    支持三种写法：字符串、``{"path": ..., "mode": ..., "batch": ...}``、
    ``(path, mode)`` 二元组。
    """
    if isinstance(raw, str):
        path, mode, batch, source = raw, default_mode, None, None
    elif isinstance(raw, dict):
        path = raw.get("path", raw.get("file", raw.get("name")))
        mode = raw.get("mode", raw.get("access", default_mode))
        batch = raw.get("batch", raw.get("batch_id"))
        source = raw.get("from", raw.get("source", raw.get("old")))
    elif isinstance(raw, (list, tuple)):
        if not raw:
            return None
        path = raw[0]
        mode = raw[1] if len(raw) > 1 else default_mode
        batch = None
        source = None
    else:
        path, mode, batch, source = raw, default_mode, None, None

    path = _norm_path(path)
    if not path:
        return None
    return {
        "path": path,
        "mode": _norm_mode(mode, default_mode),
        "batch": _norm_batch(batch if batch is not None else job_batch),
        "from": _norm_path(source) if source else "",
    }


def _parse_entries(raw_entries, default_mode: str, job_batch: str) -> list[dict]:
    if raw_entries is None:
        return []
    if isinstance(raw_entries, (str, dict)):
        raw_entries = [raw_entries]
    entries = []
    for raw in raw_entries:
        entry = _norm_entry(raw, default_mode, job_batch)
        if entry is not None:
            entries.append(entry)
    return entries


def _parse_renames(raw_renames) -> list[tuple[str, str]]:
    """解析改名声明，支持 dict / 二元组 / 列表 / ``"old -> new"`` 字符串。"""
    if raw_renames is None:
        return []
    if isinstance(raw_renames, dict):
        raw_renames = list(raw_renames.items())
    elif (
        isinstance(raw_renames, tuple)
        and len(raw_renames) == 2
        and all(isinstance(item, str) for item in raw_renames)
    ):
        raw_renames = [raw_renames]
    elif isinstance(raw_renames, str):
        if "->" in raw_renames:
            return _parse_renames([raw_renames.split("->", 1)])
        raw_renames = [raw_renames]
    renames = []
    for raw in raw_renames:
        if isinstance(raw, str) and "->" in raw:
            raw = raw.split("->", 1)
        if isinstance(raw, dict):
            old = raw.get("from", raw.get("old", raw.get("source")))
            new = raw.get("to", raw.get("new", raw.get("target")))
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            old, new = raw[0], raw[1]
        else:
            continue
        old = _norm_path(old)
        new = _norm_path(new)
        if old and new and old != new:
            renames.append((old, new))
    return renames


def _parse_explicit_edges(raw_edges, job_batch: str) -> list[dict]:
    if not raw_edges:
        return []
    if isinstance(raw_edges, dict):
        raw_edges = [
            # {"a": "b"} 或 {"a": {"target": "b", "mode": "r"}}
            {**value, "source": key} if isinstance(value, dict) else {"source": key, "target": value}
            for key, value in raw_edges.items()
        ]
    edges = []
    for raw in raw_edges:
        if isinstance(raw, dict):
            source = raw.get("source", raw.get("from", raw.get("input")))
            target = raw.get("target", raw.get("to", raw.get("output")))
            read_mode = raw.get("read_mode", raw.get("mode"))
            write_mode = raw.get("write_mode", raw.get("mode"))
            batch = raw.get("batch", raw.get("batch_id"))
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            source, target = raw[0], raw[1]
            read_mode = raw[2] if len(raw) > 2 else None
            write_mode = read_mode
            batch = raw[3] if len(raw) > 3 else None
        else:
            continue
        source = _norm_path(source)
        target = _norm_path(target)
        if not source or not target:
            continue
        edges.append({
            "source": source,
            "target": target,
            "read_mode": _norm_mode(read_mode, DEFAULT_READ_MODE),
            "write_mode": _norm_mode(write_mode, DEFAULT_WRITE_MODE),
            "batch": _norm_batch(batch if batch is not None else job_batch),
        })
    return edges


def _normalize_job(job: dict) -> dict:
    job = job or {}
    job_id = str(job.get("id", job.get("job", "")))
    batch = _norm_batch(job.get("batch", job.get("batch_id")))
    reads = _parse_entries(job.get("reads", []), DEFAULT_READ_MODE, batch)
    writes = _parse_entries(job.get("writes", []), DEFAULT_WRITE_MODE, batch)
    renames = _parse_renames(job.get("renames", job.get("renamed")))
    explicit_edges = _parse_explicit_edges(
        job.get("edges", job.get("links", job.get("dependencies"))), batch
    )

    # 写模式为 rename/move 时按改名处理：显式 from 优先，否则把读文件视作旧名。
    for write in writes:
        if write["mode"].lower() not in _RENAME_MODES:
            continue
        if write["from"]:
            renames.append((write["from"], write["path"]))
        else:
            for read in reads:
                if read["path"] != write["path"]:
                    renames.append((read["path"], write["path"]))

    return {
        "id": job_id,
        "batch": batch,
        "reads": reads,
        "writes": writes,
        "renames": renames,
        "explicit_edges": explicit_edges,
    }


def _resolve_aliases(renames: list[tuple[str, str]]) -> dict[str, str]:
    """建立旧名 -> 当前名的映射，并顺着改名链解析到最终节点。"""
    alias: dict[str, str] = {}
    for old, new in renames:
        alias[old] = new

    resolved: dict[str, str] = {}

    def resolve(path: str) -> str:
        if path in resolved:
            return resolved[path]
        seen = set()
        current = path
        while current in alias and current not in seen:
            seen.add(current)
            current = alias[current]
        # 改名链成环时停在环上，至少保证映射可终止且不丢节点。
        resolved[path] = current
        return current

    for old in alias:
        resolve(old)
    return resolved


def _canonical(path: str) -> str:
    return _graph.get("canonical", {}).get(path, path)


def _ensure_graph() -> dict:
    global _graph, _dirty
    if not _dirty:
        return _graph
    _rebuild_graph()
    return _graph


def _job_derived_edges(job: dict) -> list[dict]:
    """把任务血缘展开成未规范化的派生边：显式边 + 读写的笛卡尔积。"""
    job_edges = [
        {
            "source": raw["source"],
            "target": raw["target"],
            "batch": raw["batch"],
            "read_mode": raw["read_mode"],
            "write_mode": raw["write_mode"],
        }
        for raw in job["explicit_edges"]
    ]
    for read in job["reads"]:
        for write in job["writes"]:
            job_edges.append({
                "source": read["path"],
                "target": write["path"],
                "batch": write["batch"] or read["batch"] or job["batch"],
                "read_mode": read["mode"],
                "write_mode": write["mode"],
            })
    return job_edges


def _rebuild_graph() -> None:
    """从任务级血缘重建文件级图：归集边、合并改名节点。"""
    global _graph, _dirty

    all_renames = []
    for job in _jobs.values():
        all_renames.extend(job["renames"])
    canonical_map = _resolve_aliases(all_renames)

    def canon(path: str) -> str:
        return canonical_map.get(path, path)

    nodes: dict[str, None] = {}
    edges: dict[tuple[str, str, str], dict] = {}
    downstream: dict[str, dict[str, set]] = {}
    upstream: dict[str, dict[str, set]] = {}

    for job in _jobs.values():
        for raw in _job_derived_edges(job):
            source = canon(raw["source"])
            target = canon(raw["target"])
            if not source or not target:
                continue
            nodes[source] = None
            nodes[target] = None
            # 改名后同一文件不应再形成自环派生边。
            if source == target:
                continue
            batch = raw["batch"]
            key = (source, target, batch)
            edge = edges.get(key)
            if edge is None:
                edge = {
                    "source": source,
                    "target": target,
                    "batch": batch,
                    "read_modes": set(),
                    "write_modes": set(),
                    "jobs": [],
                    "count": 0,
                }
                edges[key] = edge
            edge["read_modes"].add(raw["read_mode"])
            edge["write_modes"].add(raw["write_mode"])
            if job["id"] not in edge["jobs"]:
                edge["jobs"].append(job["id"])
            edge["count"] += 1
            downstream.setdefault(source, {}).setdefault(target, set()).add(batch)
            upstream.setdefault(target, {}).setdefault(source, set()).add(batch)

    # 改名目标也保留为节点，但要挂到改名链的最终节点上，改名本身不产生派生边。
    for old, new in all_renames:
        if new:
            nodes[canon(new)] = None

    _graph = {
        "nodes": nodes,
        "edges": edges,
        "downstream": downstream,
        "upstream": upstream,
        "aliases": {old: new for old, new in canonical_map.items() if old != new},
        "canonical": canonical_map,
    }
    _dirty = False


def _canonical_for(job: dict) -> dict[str, str]:
    """用当前所有任务的改名声明解析别名；record 无需整图重建。"""
    renames = []
    for other in _jobs.values():
        renames.extend(other["renames"])
    return _resolve_aliases(renames)


def record(job) -> dict:
    """记录一个任务的读写文件，并把它聚合进文件级血缘图。

    任务字段：``id``、``reads``、``writes``，可选 ``batch``、``renames``、
    ``edges``。读写条目可写成路径字符串，或 ``{"path", "mode", "batch"}``。
    返回该任务贡献的文件级边、批次与改名数量。
    """
    global _dirty
    normalized = _normalize_job(job if isinstance(job, dict) else {})
    _jobs[normalized["id"]] = normalized
    _dirty = True

    canonical_map = _canonical_for(normalized)
    seen: dict[tuple[str, str, str], None] = {}
    for raw in _job_derived_edges(normalized):
        source = canonical_map.get(raw["source"], raw["source"])
        target = canonical_map.get(raw["target"], raw["target"])
        if not source or not target or source == target:
            continue
        seen[(source, target, raw["batch"])] = None

    return {
        "job": normalized["id"],
        "reads": len(normalized["reads"]),
        "writes": len(normalized["writes"]),
        "edges": len(seen),
        "batch": normalized["batch"],
        "renames": len(normalized["renames"]),
        "path": [[source, target] for source, target, _ in seen],
    }


def _normalize_direction(direction) -> str:
    if isinstance(direction, (list, tuple, set)):
        values = {str(item).strip().lower() for item in direction}
        if "both" in values or "all" in values or _DIRECTIONS <= values:
            return "both"
        direction = next(iter(values)) if values else "both"
    text = str(direction or "both").strip().lower()
    if text in {"both", "all", "any", "bidirectional", "bothways", "all-directions"}:
        return "both"
    if text in {"down", "downstream", "out", "output", "forward", "影响"}:
        return "downstream"
    if text in {"up", "upstream", "in", "input", "backward", "来源"}:
        return "upstream"
    raise ValueError(f"unknown direction: {direction}")


def _normalize_batches(batch) -> set | None:
    if batch is None:
        return None
    if isinstance(batch, (list, tuple, set)):
        return {_norm_batch(item) for item in batch}
    return {_norm_batch(batch)}


def _under_match(path: str, prefix: str | None) -> bool:
    if prefix is None:
        return True
    return path == prefix or path.startswith(prefix + "/")


def _succ(node: str, direction: str, batches: set | None) -> list[tuple[str, dict]]:
    adjacency = _graph["downstream" if direction == "downstream" else "upstream"]
    result = []
    for target, edge_batches in adjacency.get(node, {}).items():
        matched = edge_batches if batches is None else edge_batches & batches
        if matched:
            result.append((target, matched))
    result.sort(key=lambda item: item[0])
    return result


def _edge_info(source: str, target: str, matched_batches: set) -> tuple[list[str], list[str]]:
    """按派生方向取边上的任务和批次；source 必须是边的起点。"""
    jobs: list[str] = []
    batches: list[str] = []
    for batch in sorted(matched_batches):
        edge = _graph["edges"].get((source, target, batch))
        if edge is None:
            continue
        batches.append(batch)
        for job in edge["jobs"]:
            if job not in jobs:
                jobs.append(job)
    return jobs, batches


def _adjacent(node: str, other: str, direction: str, matched: set) -> tuple[list[str], list[str]]:
    """把遍历方向还原成边的真实起点/终点，再取边信息。"""
    if direction == "downstream":
        return _edge_info(node, other, matched)
    return _edge_info(other, node, matched)


def _walk(origin: str, direction: str, max_depth: int | None, batches: set | None) -> dict:
    """在文件图上做去重 BFS；遇到环时截断并保留入口节点。"""
    nodes: dict[str, dict] = {}
    paths: list[list[str]] = []
    cycles: list[dict] = []
    breakpoints: list[dict] = []
    visited = {origin}
    truncated = False
    queue = deque([(origin, 0, (origin,))])

    while queue:
        node, depth, chain = queue.popleft()
        successors = _succ(node, direction, batches)
        if not successors and direction == "upstream" and depth > 0:
            # 向上追溯时某个文件没有生产任务，是血缘断链的断点。
            breakpoints.append({
                "path": node,
                "depth": depth,
                "direction": direction,
                "reason": "missing_producer",
            })
        for target, matched_batches in successors:
            next_depth = depth + 1
            if max_depth is not None and next_depth > max_depth:
                truncated = True
                continue
            jobs, edge_batches = _adjacent(node, target, direction, matched_batches)
            if target in chain:
                # 环路截断：记录回边，不继续展开；回到入口节点时保留该节点。
                cycle = {
                    "node": target,
                    "depth": next_depth,
                    "direction": direction,
                    "entry": target == origin,
                    "from": node,
                    "path": list(chain) + [target],
                    "jobs": jobs,
                    "batches": edge_batches,
                }
                cycles.append(cycle)
                truncated = True
                # 环路截断保留入口（闭环回到的）节点，也标记环上被回指的节点。
                existing = nodes.get(target)
                if existing is not None:
                    existing["cycle"] = True
                elif target == origin:
                    nodes[origin] = {
                        "path": origin,
                        "depth": next_depth,
                        "direction": direction,
                        "cycle": True,
                        "jobs": jobs,
                        "batches": edge_batches,
                    }
                continue
            if target in visited:
                # 多跳结果去重：同一文件只保留首次（最短）到达。
                continue
            visited.add(target)
            nodes[target] = {
                "path": target,
                "depth": next_depth,
                "direction": direction,
                "cycle": False,
                "jobs": jobs,
                "batches": edge_batches,
            }
            new_chain = chain + (target,)
            paths.append(list(new_chain))
            queue.append((target, next_depth, new_chain))

    return {
        "nodes": nodes,
        "paths": paths,
        "cycles": cycles,
        "breakpoints": breakpoints,
        "truncated": truncated,
    }


def impact(path, direction="both", max_depth=None, batch=None, under=None, **kwargs) -> dict:
    """查询文件的影响面，默认同时返回上下游。

    参数：
      - ``direction``: ``downstream`` / ``upstream`` / ``both``
      - ``max_depth``: 最大跳数，``None`` 表示不限
      - ``batch``: 只走指定批次（可传字符串或列表）
      - ``under``: 只保留落在该目录下的结果节点
    兼容别名：``depth``/``levels``/``max_hops``/``hops`` 等价于 ``max_depth``，
    ``batch_id``/``batches`` 等价于 ``batch``，``dir``/``directory``/``prefix``
    等价于 ``under``。
    返回层级（``depth``）、节点（``nodes``/``downstream``/``upstream``）、
    路径（``paths``/``upstream_paths``）、断点（``breakpoints``）和环路信息。
    """
    if kwargs:
        if "depth" in kwargs:
            max_depth = kwargs.pop("depth")
        if "levels" in kwargs:
            max_depth = kwargs.pop("levels")
        if "max_hops" in kwargs:
            max_depth = kwargs.pop("max_hops")
        if "hops" in kwargs:
            max_depth = kwargs.pop("hops")
        if "batch_id" in kwargs:
            batch = kwargs.pop("batch_id")
        if "batches" in kwargs:
            batch = kwargs.pop("batches")
        for name in ("dir", "directory", "prefix", "path_prefix"):
            if name in kwargs:
                under = kwargs.pop(name)
        if "directions" in kwargs:
            direction = kwargs.pop("directions")
        if kwargs:
            raise TypeError(f"unexpected impact arguments: {', '.join(sorted(kwargs))}")

    _ensure_graph()
    query = _norm_path(path)
    origin = _canonical(query)
    norm_direction = _normalize_direction(direction)
    batches = _normalize_batches(batch)
    prefix = _norm_path(under) if under is not None else None
    if prefix == "":
        prefix = None

    if max_depth is not None:
        try:
            max_depth = int(max_depth)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_depth must be an integer or None") from exc
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")

    walk_down = {"nodes": {}, "paths": [], "cycles": [], "breakpoints": [], "truncated": False}
    walk_up = {"nodes": {}, "paths": [], "cycles": [], "breakpoints": [], "truncated": False}
    if norm_direction in {"downstream", "both"}:
        walk_down = _walk(origin, "downstream", max_depth, batches)
    if norm_direction in {"upstream", "both"}:
        walk_up = _walk(origin, "upstream", max_depth, batches)

    def keep(item: dict) -> bool:
        return _under_match(item["path"], prefix)

    down_nodes = [item for item in walk_down["nodes"].values() if keep(item)]
    up_nodes = [item for item in walk_up["nodes"].values() if keep(item)]
    down_paths = [chain for chain in walk_down["paths"] if _under_match(chain[-1], prefix)]
    up_paths = [chain for chain in walk_up["paths"] if _under_match(chain[-1], prefix)]
    down_cycles = [item for item in walk_down["cycles"] if _under_match(item["node"], prefix)]
    up_cycles = [item for item in walk_up["cycles"] if _under_match(item["node"], prefix)]
    down_breaks = [item for item in walk_down["breakpoints"] if _under_match(item["path"], prefix)]
    up_breaks = [item for item in walk_up["breakpoints"] if _under_match(item["path"], prefix)]

    # 同一文件可能同时出现在上下游，combined 里按路径合并并保留每层深度。
    combined: dict[str, dict] = {}
    for item in down_nodes + up_nodes:
        entry = combined.get(item["path"])
        if entry is None:
            combined[item["path"]] = {
                "path": item["path"],
                "depth": item["depth"],
                "directions": [item["direction"]],
                "layers": {item["direction"]: item["depth"]},
                "cycle": item["cycle"],
                "jobs": list(item["jobs"]),
                "batches": list(item["batches"]),
            }
        else:
            if item["direction"] not in entry["directions"]:
                entry["directions"].append(item["direction"])
            entry["layers"][item["direction"]] = item["depth"]
            entry["depth"] = min(entry["depth"], item["depth"])
            entry["cycle"] = entry["cycle"] or item["cycle"]
            for job in item["jobs"]:
                if job not in entry["jobs"]:
                    entry["jobs"].append(job)
            for item_batch in item["batches"]:
                if item_batch not in entry["batches"]:
                    entry["batches"].append(item_batch)

    # 深度取真实结果节点的最大层级；入口节点因闭环被保留时不抬高深度。
    depth = 0
    for item in down_nodes + up_nodes:
        if item["path"] != origin:
            depth = max(depth, item["depth"])
    for item in down_cycles + up_cycles:
        if item["node"] != origin:
            depth = max(depth, item["depth"])

    # 直接任务：下游方向是读入口文件的任务，上游方向是产出入口文件的任务。
    # 与 `under` 过滤无关：它描述的是入口文件自身的直接关系。
    direct_jobs: list[str] = []
    for walk_direction in ("downstream", "upstream"):
        if norm_direction not in {walk_direction, "both"}:
            continue
        for target, matched in _succ(origin, walk_direction, batches):
            jobs, _ = _adjacent(origin, target, walk_direction, matched)
            for job in jobs:
                if job not in direct_jobs:
                    direct_jobs.append(job)

    return {
        "path": origin,
        "query": query,
        "direction": norm_direction,
        "depth": depth,
        "jobs": direct_jobs,
        "downstream": down_nodes,
        "upstream": up_nodes,
        "nodes": list(combined.values()),
        "paths": down_paths,
        "upstream_paths": up_paths,
        "breakpoints": down_breaks + up_breaks,
        "cycles": down_cycles + up_cycles,
        "truncated": walk_down["truncated"] or walk_up["truncated"],
        "batch": batch,
        "under": prefix,
        "origin": {
            "path": origin,
            "query": query,
            "renamed": origin != query,
        },
    }


def upstream(path, **kwargs) -> dict:
    """只查上游来源。"""
    return impact(path, direction="upstream", **kwargs)


def downstream(path, **kwargs) -> dict:
    """只查下游影响。"""
    return impact(path, direction="downstream", **kwargs)


def lineage_path(source, target, batch=None, **kwargs) -> dict:
    """追踪 source 到 target 的一条下游派生路径。

    路径断开时返回可达的末端节点作为断点，便于定位断链位置。
    """
    if kwargs:
        if "batch_id" in kwargs:
            batch = kwargs.pop("batch_id")
        if kwargs:
            raise TypeError(f"unexpected lineage_path arguments: {', '.join(sorted(kwargs))}")
    _ensure_graph()
    source_path = _canonical(_norm_path(source))
    target_path = _canonical(_norm_path(target))
    batches = _normalize_batches(batch)
    if source_path == target_path:
        exists = source_path in _graph["nodes"]
        return {
            "source": source_path,
            "target": target_path,
            "connected": exists,
            "path": [source_path] if exists else [],
            "depth": 0,
            "breakpoints": [] if exists else [{
                "path": source_path,
                "depth": 0,
                "reason": "unknown_file",
            }],
            "truncated": False,
        }

    visited = {source_path: [source_path]}
    queue = deque([source_path])
    while queue:
        node = queue.popleft()
        chain = visited[node]
        for successor, _ in _succ(node, "downstream", batches):
            if successor not in visited:
                visited[successor] = chain + [successor]
                if successor == target_path:
                    return {
                        "source": source_path,
                        "target": target_path,
                        "connected": True,
                        "path": visited[successor],
                        "depth": len(visited[successor]) - 1,
                        "breakpoints": [],
                        "truncated": False,
                    }
                queue.append(successor)

    leaves = []
    for node, chain in visited.items():
        if not _succ(node, "downstream", batches):
            leaves.append({
                "path": node,
                "depth": len(chain) - 1,
                "reason": "target_not_reached",
            })
    if not leaves:
        # 可达子图全在环里，没有天然末端：把最深链尾当作截断点。
        deepest = max(visited.items(), key=lambda item: (len(item[1]), item[0]))
        leaves.append({
            "path": deepest[0],
            "depth": len(deepest[1]) - 1,
            "reason": "cycle_without_target",
        })
    leaves.sort(key=lambda item: (-item["depth"], item["path"]))
    return {
        "source": source_path,
        "target": target_path,
        "connected": False,
        "path": [],
        "depth": leaves[0]["depth"] if leaves else 0,
        "breakpoints": leaves,
        "truncated": False,
    }


def job_lineage(job_id) -> dict:
    """把单个任务级血缘展开成文件级读写与边视图。"""
    _ensure_graph()
    job_id = str(job_id)
    job = _jobs.get(job_id)
    if job is None:
        return {"job": job_id, "exists": False, "reads": [], "writes": [], "edges": []}

    def canon_entry(entry):
        return {
            "path": _canonical(entry["path"]),
            "mode": entry["mode"],
            "batch": entry["batch"],
        }

    edges = []
    for edge in _graph["edges"].values():
        if job_id in edge["jobs"]:
            edges.append({
                "source": edge["source"],
                "target": edge["target"],
                "batch": edge["batch"],
                "read_modes": sorted(edge["read_modes"]),
                "write_modes": sorted(edge["write_modes"]),
                "jobs": list(edge["jobs"]),
                "count": edge["count"],
            })
    edges.sort(key=lambda item: (item["source"], item["target"], item["batch"]))
    return {
        "job": job_id,
        "exists": True,
        "batch": job["batch"],
        "reads": [canon_entry(entry) for entry in job["reads"]],
        "writes": [canon_entry(entry) for entry in job["writes"]],
        "renames": [
            {"from": old, "to": _canonical(old), "raw_to": new}
            for old, new in job["renames"]
        ],
        "edges": edges,
    }


def export_graph(path=None) -> dict:
    """导出可 JSON 序列化的文件级血缘图；传入路径时同时写文件。"""
    _ensure_graph()
    nodes = []
    aliases_by_node: dict[str, list[str]] = {}
    for old, new in _graph["aliases"].items():
        aliases_by_node.setdefault(new, []).append(old)

    for node in sorted(_graph["nodes"]):
        nodes.append({
            "path": node,
            "aliases": sorted(aliases_by_node.get(node, [])),
        })

    edges = []
    for edge in sorted(_graph["edges"].values(), key=lambda item: (item["source"], item["target"], item["batch"])):
        edges.append({
            "source": edge["source"],
            "target": edge["target"],
            "batch": edge["batch"],
            "read_modes": sorted(edge["read_modes"]),
            "write_modes": sorted(edge["write_modes"]),
            "jobs": list(edge["jobs"]),
            "count": edge["count"],
        })

    graph = {
        "nodes": nodes,
        "edges": edges,
        "aliases": dict(sorted(_graph["aliases"].items())),
        "batches": sorted({edge["batch"] for edge in edges}),
        "jobs": sorted(job["id"] for job in _jobs.values()),
    }
    if path is not None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(graph, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    return graph


def reset() -> None:
    """清空已记录的任务与血缘图（主要供测试使用）。"""
    global _jobs, _graph, _dirty
    _jobs = {}
    _graph = {}
    _dirty = True
