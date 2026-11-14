"""依赖图环检测与断边建议。

图格式（节点名必须是字符串）：
    {"a": ["b"]}          # 无权边，权重视为 1.0
    {"a": {"b": 2.5}}     # 带权边
    {"a": [("b", 2.5)]}   # 带权边的另一种写法

检测口径：先用入度剥离（Kahn）找出环上节点，再在诱导子图上算强连通
分量，最后枚举每个分量内的所有基本环，输出闭合环路径。

断边口径：对每个强连通分量求"断边数最少、并列时总权重最小"的断边集；
分量边数超过 _EXACT_EDGE_LIMIT 时退化为贪心近似。受影响节点为该分量
内全部互相等待的节点。
"""

import itertools

__all__ = ["check", "suggest_breaks", "CycleChecker", "GraphError"]

_EXACT_EDGE_LIMIT = 16


class GraphError(ValueError):
    """图结构非法（未知节点、权重非法、格式错误）时抛出。"""


def _parse_succs(node, succs):
    if isinstance(succs, dict):
        items = list(succs.items())
    elif isinstance(succs, (list, tuple)):
        items = []
        for item in succs:
            if isinstance(item, str):
                items.append((item, 1.0))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                items.append((item[0], item[1]))
            else:
                raise GraphError("节点 %r 的后继 %r 无法识别" % (node, item))
    else:
        raise GraphError("节点 %r 的后继必须是列表或字典，收到 %r" % (node, succs))
    edges = {}
    for target, weight in items:
        if not isinstance(target, str):
            raise GraphError("节点 %r 的后继 %r 不是字符串" % (node, target))
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise GraphError("边 %r -> %r 的权重必须是数字，收到 %r" % (node, target, weight))
        if weight <= 0:
            raise GraphError("边 %r -> %r 的权重必须是正数，收到 %r" % (node, target, weight))
        edges[target] = float(weight)
    return edges


def _normalize(graph):
    if not isinstance(graph, dict):
        raise GraphError("graph 必须是 {节点: 后继} 的字典，收到 %r" % (graph,))
    norm = {}
    for node, succs in graph.items():
        if not isinstance(node, str):
            raise GraphError("节点名必须是字符串，收到 %r" % (node,))
        norm[node] = _parse_succs(node, succs)
    for node, edges in norm.items():
        for target in edges:
            if target not in norm:
                raise GraphError("节点 %r 依赖了不存在的节点 %r" % (node, target))
    return norm


def _cyclic_nodes(norm, nodes):
    """入度剥离：不断移除入度为 0 的节点，剩下的都在环上。"""
    indeg = {n: 0 for n in nodes}
    for n in nodes:
        for t in norm[n]:
            if t in indeg:
                indeg[t] += 1
    queue = [n for n in nodes if indeg[n] == 0]
    while queue:
        n = queue.pop()
        for t in norm[n]:
            if t in indeg and indeg[t] > 0:
                indeg[t] -= 1
                if indeg[t] == 0:
                    queue.append(t)
    return {n for n in nodes if indeg[n] > 0}


def _sccs(norm, nodes):
    """迭代版 Tarjan，返回 nodes 诱导子图的全部强连通分量。"""
    index_of = {}
    lowlink = {}
    on_stack = set()
    stack = []
    result = []
    for root in sorted(nodes):
        if root in index_of:
            continue
        index_of[root] = lowlink[root] = len(index_of)
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(sorted(norm[root])))]
        while work:
            node, it = work[-1]
            descended = False
            for nxt in it:
                if nxt not in nodes:
                    continue
                if nxt not in index_of:
                    index_of[nxt] = lowlink[nxt] = len(index_of)
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(norm[nxt]))))
                    descended = True
                    break
                if nxt in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[nxt])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                scc = []
                while True:
                    top = stack.pop()
                    on_stack.discard(top)
                    scc.append(top)
                    if top == node:
                        break
                result.append(scc)
    return result


def _elementary_cycles(norm, scc):
    """枚举分量内所有基本环，每个环只从字典序最小的节点出发一次。"""
    members = sorted(scc)
    cycles = []
    for i, start in enumerate(members):
        allowed = set(members[i:])
        stack = [(start, [start])]
        while stack:
            node, path = stack.pop()
            for nxt in sorted(norm[node]):
                if nxt == start:
                    cycles.append(path + [start])
                elif nxt in allowed and nxt not in path:
                    stack.append((nxt, path + [nxt]))
    return cycles


def _find_cycles(norm, only=None):
    nodes = set(norm) if only is None else set(only)
    cyclic = _cyclic_nodes(norm, nodes)
    cycles = []
    for scc in _sccs(norm, cyclic):
        if len(scc) > 1:
            cycles.extend(_elementary_cycles(norm, set(scc)))
        elif scc[0] in norm[scc[0]]:
            cycles.append([scc[0], scc[0]])
    cycles.sort()
    return cycles


def _is_acyclic(norm, scc, removed):
    indeg = {n: 0 for n in scc}
    for n in scc:
        for t in norm[n]:
            if t in indeg and (n, t) not in removed:
                indeg[t] += 1
    queue = [n for n in scc if indeg[n] == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for t in norm[n]:
            if t in indeg and (n, t) not in removed:
                indeg[t] -= 1
                if indeg[t] == 0:
                    queue.append(t)
    return seen == len(scc)


def _min_break_edges(norm, scc, edges):
    if len(edges) <= _EXACT_EDGE_LIMIT:
        for size in range(1, len(edges) + 1):
            combos = sorted(
                itertools.combinations(edges, size),
                key=lambda combo: sum(norm[u][v] for u, v in combo),
            )
            for combo in combos:
                if _is_acyclic(norm, scc, set(combo)):
                    return list(combo)
        return list(edges)
    removed = []
    while not _is_acyclic(norm, scc, set(removed)):
        cycle = _find_cycles(norm, only=scc)[0]
        cheapest = min(
            zip(cycle, cycle[1:]),
            key=lambda edge: norm[edge[0]][edge[1]],
        )
        removed.append(cheapest)
    return removed


def _suggest(norm, only=None):
    nodes = set(norm) if only is None else set(only)
    cyclic = _cyclic_nodes(norm, nodes)
    suggestions = []
    for scc in _sccs(norm, cyclic):
        scc_set = set(scc)
        edges = [(u, v) for u in scc for v in norm[u] if v in scc_set]
        if len(scc) == 1 and scc[0] not in norm[scc[0]]:
            continue
        affected = sorted(scc_set)
        for u, v in _min_break_edges(norm, scc_set, edges):
            suggestions.append({
                "edge": (u, v),
                "weight": norm[u][v],
                "affected": affected,
                "affected_count": len(affected),
            })
    suggestions.sort(key=lambda s: (s["weight"], s["edge"]))
    return suggestions


def check(graph):
    """返回图中所有基本环，每个环是闭合节点路径，如 ["a", "b", "a"]。"""
    return _find_cycles(_normalize(graph))


def suggest_breaks(graph):
    """返回断边建议列表，每项含 edge、weight、affected、affected_count。"""
    return _suggest(_normalize(graph))


def _reachable(norm, start, reverse=False):
    seen = {start}
    stack = [start]
    while stack:
        node = stack.pop()
        if reverse:
            nxts = [n for n in norm if node in norm[n]]
        else:
            nxts = norm[node]
        for nxt in nxts:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


class CycleChecker:
    """持有图状态，支持热更新前后的增量环检查。""" 

    def __init__(self, graph):
        self._norm = _normalize(graph)

    def check(self):
        return _find_cycles(self._norm)

    def suggest_breaks(self):
        return _suggest(self._norm)

    def _prepare(self, node, succs):
        if node not in self._norm:
            raise GraphError("热更新的节点 %r 不在图中" % (node,))
        edges = _parse_succs(node, succs)
        for target in edges:
            if target not in self._norm:
                raise GraphError("节点 %r 依赖了不存在的节点 %r" % (node, target))
        return edges

    def _cycles_through(self, node):
        region = _reachable(self._norm, node) & _reachable(self._norm, node, reverse=True)
        return _find_cycles(self._norm, only=region)

    def preview(self, node, succs):
        """热更新前检查：假设把 node 的依赖改为 succs，返回会产生的环。"""
        edges = self._prepare(node, succs)
        old = self._norm[node]
        self._norm[node] = edges
        try:
            return self._cycles_through(node)
        finally:
            self._norm[node] = old

    def update(self, node, succs):
        """应用热更新并增量检查，只重算经过 node 的环，返回更新后的环。"""
        self._norm[node] = self._prepare(node, succs)
        return self._cycles_through(node)
