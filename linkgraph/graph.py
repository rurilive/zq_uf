"""站点链接图的环路分析：归一化建图，迭代识别自环、二元环和强连通分量。"""

from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

_edges = {}


def normalize(url):
    """归一化链接：scheme/host 小写，去掉 query 和 fragment，去尾部斜杠。"""
    if not isinstance(url, str):
        raise TypeError(f"链接必须是字符串，收到 {type(url).__name__}")
    text = url.strip()
    if not text:
        raise ValueError("链接不能是空白字符串")
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise ValueError(f"无法解析的链接 {url!r}: {exc}") from exc
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if netloc and not path:
        path = "/"
    if len(path) > 1:
        path = path.rstrip("/")
    if scheme or netloc:
        return urlunsplit((scheme, netloc, path, "", ""))
    return path


def build(pages):
    """建图：返回原始邻接表（保留重复出链），内部按归一化键存去重后的图。"""
    if not isinstance(pages, Mapping):
        raise TypeError(f"pages 必须是 {{url: [出链]}} 的映射，收到 {type(pages).__name__}")
    raw = {}
    graph = {}
    for url, links in pages.items():
        src = normalize(url)
        if isinstance(links, (str, bytes)) or not hasattr(links, "__iter__"):
            raise TypeError(f"页面 {url!r} 的出链必须是可迭代的链接列表")
        link_list = list(links)
        raw[url] = link_list
        targets = graph.setdefault(src, set())
        for link in link_list:
            dst = normalize(link) or src
            targets.add(dst)
            graph.setdefault(dst, set())
    _edges.clear()
    _edges.update(graph)
    return raw


def cycles(limit=None):
    """环路分析。limit 为遍历节点上限；不传 limit 返回 []（兼容旧接口）。

    返回 {"nodes", "edges", "cycles", "truncated"}，每个环含 path 和 nodes。
    """
    if limit is None:
        return []
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError(f"limit 必须是正整数，收到 {type(limit).__name__}")
    if limit < 1:
        raise ValueError(f"limit 必须 >= 1，收到 {limit}")
    nodes = set(_edges)
    edge_count = 0
    for src, targets in _edges.items():
        edge_count += len(targets)
        nodes.update(targets)
    sccs, truncated = _strongly_connected_components(nodes, _edges, limit)
    found = []
    for scc in sorted(sccs, key=min):
        members = sorted(scc)
        if len(scc) == 1:
            node = members[0]
            if node in _edges.get(node, ()):
                found.append({"path": [node], "nodes": [node]})
        else:
            path = _find_cycle(members[0], set(scc), _edges)
            found.append({"path": path, "nodes": members})
    return {
        "nodes": len(nodes),
        "edges": edge_count,
        "cycles": found,
        "truncated": truncated,
    }


def _strongly_connected_components(nodes, edges, limit):
    """迭代式 Tarjan，访问节点数超过 limit 时截断。"""
    index = {}
    lowlink = {}
    stacked = set()
    stack = []
    sccs = []
    counter = 0
    truncated = False
    for root in sorted(nodes):
        if truncated:
            break
        if root in index:
            continue
        index[root] = lowlink[root] = counter
        counter += 1
        if counter > limit:
            truncated = True
            break
        stack.append(root)
        stacked.add(root)
        work = [(root, iter(sorted(edges.get(root, ()))))]
        while work:
            node, pending = work[-1]
            descended = False
            for nxt in pending:
                if nxt not in index:
                    index[nxt] = lowlink[nxt] = counter
                    counter += 1
                    if counter > limit:
                        truncated = True
                        break
                    stack.append(nxt)
                    stacked.add(nxt)
                    work.append((nxt, iter(sorted(edges.get(nxt, ())))))
                    descended = True
                    break
                if nxt in stacked and index[nxt] < lowlink[node]:
                    lowlink[node] = index[nxt]
            if truncated:
                break
            if descended:
                continue
            work.pop()
            if lowlink[node] == index[node]:
                scc = []
                while True:
                    member = stack.pop()
                    stacked.discard(member)
                    scc.append(member)
                    if member == node:
                        break
                sccs.append(scc)
            if work and lowlink[node] < lowlink[work[-1][0]]:
                lowlink[work[-1][0]] = lowlink[node]
    return sccs, truncated


def _find_cycle(start, members, edges):
    """在强连通分量内从最小节点迭代找一条回到起点的环路径。"""
    parent = {start: None}
    queue = [start]
    while queue:
        node = queue.pop()
        for nxt in sorted(edges.get(node, ())):
            if nxt not in members:
                continue
            if nxt == start:
                path = []
                cursor = node
                while cursor is not None:
                    path.append(cursor)
                    cursor = parent[cursor]
                path.reverse()
                return path
            if nxt not in parent:
                parent[nxt] = node
                queue.append(nxt)
    return [start]
