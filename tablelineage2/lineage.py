"""表级血缘：按任务指纹增量维护输入输出边。

- record(job)：只增改该任务贡献的边，已有边去重，重复记录幂等；
  任务重录时移除其不再声明的旧边；成环边截断并返回具体环路路径。
- remove(job)：按任务指纹只移除该任务贡献的边，并清理孤立节点。
- query(table=, since=, until=)：按表和更新时间过滤查询血缘边。

边携带任务指纹和更新时间（job["time"]，缺省取当前时间）。
"""

import hashlib
import json
import time

_edges = {}  # (src, dst) -> {"job", "fingerprint", "updated"}
_nodes = set()  # 仍被边引用的表，删除边后清理孤立节点
_jobs = {}  # job_id -> {"fingerprint", "edges": {(src, dst)}, "updated"}


def _fingerprint(job):
    payload = {
        "id": job.get("id", ""),
        "edges": sorted(
            [e.get("src"), e.get("dst")] for e in job.get("edges", [])
        ),
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _drop_edge(key):
    src, dst = key
    del _edges[key]
    for node in (src, dst):
        if not any(node in k for k in _edges):
            _nodes.discard(node)


def _find_path(start, target):
    """现有图中 start 到 target 的一条路径（BFS，邻接按字典序，结果确定）。"""
    if start == target:
        return [start]
    adj = {}
    for s, d in _edges:
        adj.setdefault(s, []).append(d)
    for outs in adj.values():
        outs.sort()
    visited = {start}
    queue = [[start]]
    while queue:
        path = queue.pop(0)
        for nxt in adj.get(path[-1], []):
            if nxt in visited:
                continue
            if nxt == target:
                return path + [nxt]
            visited.add(nxt)
            queue.append(path + [nxt])
    return None


def record(job):
    """按任务指纹增量记录血缘边，返回该任务的新增、删除、环路情况。"""
    jid = job.get("id", "")
    ts = job.get("time", time.time())
    fp = _fingerprint(job)

    declared = []
    seen = set()
    for e in job.get("edges", []):
        key = (e.get("src"), e.get("dst"))
        if key not in seen:
            seen.add(key)
            declared.append(key)
    declared_set = set(declared)

    # 同一任务重录：移除其不再声明的旧贡献（只保留最新贡献）。
    removed = 0
    old = _jobs.get(jid)
    if old and old["fingerprint"] != fp:
        for key in sorted(old["edges"] - declared_set):
            edge = _edges.get(key)
            if edge and edge["fingerprint"] == old["fingerprint"]:
                _drop_edge(key)
                removed += 1

    added = 0
    cycles = []
    contributed = set()
    for src, dst in declared:
        path = _find_path(dst, src)
        if path is not None:  # 成环，截断该边并给出具体环路路径
            cycles.append([src] + path)
            continue
        key = (src, dst)
        if key in _edges:
            edge = _edges[key]
            prev = _jobs.get(edge["job"])
            if prev and edge["job"] != jid:
                prev["edges"].discard(key)  # 贡献者变更（如任务改名）
            edge.update(job=jid, fingerprint=fp, updated=ts)
        else:
            _edges[key] = {"job": jid, "fingerprint": fp, "updated": ts}
            _nodes.update(key)
            added += 1
        contributed.add(key)

    _jobs[jid] = {"fingerprint": fp, "edges": contributed, "updated": ts}
    return {"edges": len(contributed), "added": added,
            "removed": removed, "cycle": cycles}


def remove(job):
    """按任务指纹移除该任务贡献的边，清理孤立节点。"""
    jid = job.get("id", "")
    fp = _fingerprint(job)
    removed = 0
    for e in job.get("edges", []):
        key = (e.get("src"), e.get("dst"))
        edge = _edges.get(key)
        if edge and edge["fingerprint"] == fp:
            _drop_edge(key)
            removed += 1
    entry = _jobs.get(jid)
    if entry and entry["fingerprint"] == fp:
        del _jobs[jid]
    return {"job": jid, "removed": removed, "edges": len(_edges)}


def query(table=None, since=None, until=None):
    """查询血缘边，支持按表（匹配源或目标）和更新时间区间过滤。"""
    rows = []
    for (src, dst), edge in _edges.items():
        if table is not None and table != src and table != dst:
            continue
        if since is not None and edge["updated"] < since:
            continue
        if until is not None and edge["updated"] > until:
            continue
        rows.append({
            "src": src,
            "dst": dst,
            "job": edge["job"],
            "fingerprint": edge["fingerprint"],
            "updated": edge["updated"],
        })
    rows.sort(key=lambda r: (r["src"], r["dst"]))
    return rows
