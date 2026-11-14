"""血缘影响面：多跳 + 字段级 + 环路截断 + 查询缓存。

节点口径
--------
* 表节点：表名字符串，例如 ``"raw"``。
* 字段节点：``(table, field)`` 二元组，例如 ``("raw", "id")``。

边口径
------
* 表级边存放在 ``_edges``：``table -> [table, ...]``。
* 字段级边存放在 ``_field_edges``：``(table, field) -> [(table, field), ...]``。
* ``record_field`` 会同时补齐对应的表级边，因此字段改动也能被表级查询看到。

对外接口保持 ``impact(table, field=None)`` 与 ``paths(node)``。返回值都是
``list`` 的子类，内容仍可直接与普通列表比较，额外信息挂在属性上。
"""

from collections import defaultdict

# 保留旧名字与旧结构：测试会直接清空 _edges。
_edges = {}
_field_edges = {}

# 缓存与统计。
_cache = {}
_version = 0
_stats = {"hits": 0, "misses": 0}
_kind_stats = {"impact": {"hits": 0, "misses": 0}, "paths": {"hits": 0, "misses": 0}}

# 邻接表按版本缓存，避免每次查询都重建。
_adj_cache = {}

# 路径展开的安全边界：环路本身会被截断，这些上限只防止 DAG 路径爆炸。
_MAX_DEPTH = 32
_MAX_PATHS_PER_NODE = 16
_MAX_EXPANSIONS = 20000


# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------


class ImpactResult(list):
    """下游表名单（保持 list 兼容），附带层级、路径、字段等明细。

    ``details`` 每项形如 ``{"table", "level", "paths"}``；
    ``fields``  每项形如 ``{"table", "field", "level", "paths"}``；
    ``cycles``  是检测到并截断的环路路径；
    ``field_fallback`` 表示字段查询因没有字段级出边而回退到表级。
    """

    def __init__(
        self,
        tables=(),
        *,
        details=None,
        fields=None,
        cycles=None,
        clipped=False,
        cache_hit=False,
        field_fallback=False,
    ):
        super().__init__(tables)
        self.details = details or []
        self.fields = fields or []
        self.cycles = cycles or []
        self.clipped = clipped
        self.cache_hit = cache_hit
        self.field_fallback = field_fallback

    def as_dict(self):
        return {
            "tables": list(self),
            "details": [dict(item, paths=list(item["paths"])) for item in self.details],
            "fields": [dict(item, paths=list(item["paths"])) for item in self.fields],
            "cycles": list(self.cycles),
            "clipped": self.clipped,
            "cache_hit": self.cache_hit,
            "field_fallback": self.field_fallback,
        }


class PathResult(list):
    """``paths(node)`` 的路径字符串列表，附带环路与缓存标记。"""

    def __init__(self, paths=(), *, cycles=None, clipped=False, cache_hit=False):
        super().__init__(paths)
        self.cycles = cycles or []
        self.clipped = clipped
        self.cache_hit = cache_hit


# ---------------------------------------------------------------------------
# 图数据与变更
# ---------------------------------------------------------------------------


def record(src, dst, src_field=None, dst_field=None):
    """记录一条血缘边。

    * ``record("raw", "dwd")``：表级边。
    * ``record("raw", "dwd", "id", "user_id")``：字段级边，同时补表级边。
    """
    if (src_field is None) != (dst_field is None):
        raise ValueError("src_field 与 dst_field 必须同时给出或同时省略")
    if src_field is not None:
        return record_field(src, src_field, dst, dst_field)
    _edges.setdefault(src, []).append(dst)
    _touch()
    return len(_edges[src])


def record_field(src_table, src_field, dst_table, dst_field):
    """记录字段级边，并保证对应表级边存在。返回该字段节点的出边数。"""
    src = (src_table, src_field)
    dst = (dst_table, dst_field)
    _field_edges.setdefault(src, []).append(dst)
    table_edges = _edges.setdefault(src_table, [])
    if dst_table not in table_edges:
        table_edges.append(dst_table)
    _touch()
    return len(_field_edges[src])


def _touch():
    """任何血缘变更都会让既有结果失效。"""
    global _version
    _version += 1
    _cache.clear()
    _adj_cache.clear()


def invalidate():
    """外部直接修改了图（绕过 record）后，手动让缓存失效。"""
    _touch()


def clear_cache():
    _touch()


def reset():
    """清空图、缓存与统计，主要供测试和 demo 初始化。"""
    global _version
    _edges.clear()
    _field_edges.clear()
    _cache.clear()
    _adj_cache.clear()
    _stats["hits"] = 0
    _stats["misses"] = 0
    for kind in _kind_stats.values():
        kind["hits"] = 0
        kind["misses"] = 0
    _version += 1


def cache_stats():
    """缓存命中率单独输出；hit_rate 为 0~1 的小数。"""
    total = _stats["hits"] + _stats["misses"]
    return {
        "hits": _stats["hits"],
        "misses": _stats["misses"],
        "total": total,
        "hit_rate": (_stats["hits"] / total) if total else 0.0,
        "by_kind": {
            kind: {
                "hits": counters["hits"],
                "misses": counters["misses"],
                "hit_rate": (
                    counters["hits"] / (counters["hits"] + counters["misses"])
                    if (counters["hits"] + counters["misses"])
                    else 0.0
                ),
            }
            for kind, counters in _kind_stats.items()
        },
        "size": len(_cache),
        "version": _version,
    }


def cache_hit_rate():
    return cache_stats()["hit_rate"]


# ---------------------------------------------------------------------------
# 遍历与路径
# ---------------------------------------------------------------------------


def _unique(seq):
    return sorted(set(seq))


def _table_adj():
    if "table" not in _adj_cache:
        _adj_cache["table"] = {src: _unique(dsts) for src, dsts in _edges.items()}
    return _adj_cache["table"]


def _field_adj():
    if "field" not in _adj_cache:
        _adj_cache["field"] = {src: _unique(dsts) for src, dsts in _field_edges.items()}
    return _adj_cache["field"]


def _table_rev():
    if "table_rev" not in _adj_cache:
        _adj_cache["table_rev"] = _reverse(_table_adj())
    return _adj_cache["table_rev"]


def _field_rev():
    if "field_rev" not in _adj_cache:
        _adj_cache["field_rev"] = _reverse(_field_adj())
    return _adj_cache["field_rev"]


def _reverse(adj):
    rev = defaultdict(list)
    for src, dsts in adj.items():
        for dst in dsts:
            rev[dst].append(src)
    return {node: _unique(parents) for node, parents in rev.items()}


def _label(node):
    if isinstance(node, tuple):
        return "%s.%s" % node
    return str(node)


def _fmt_chain(chain):
    return " -> ".join(_label(node) for node in chain)


def _sort_paths(path_strings):
    """路径按跳数由近到远排序，同跳数再按字典序，保证输出稳定。"""
    return sorted(set(path_strings), key=lambda s: (s.count(" -> "), s))


def _levels(source, adj):
    """最短跳数（BFS）；源节点层级为 0。"""
    levels = {source: 0}
    queue = [source]
    head = 0
    while head < len(queue):
        node = queue[head]
        head += 1
        for nxt in adj.get(node, ()):
            if nxt not in levels:
                levels[nxt] = levels[node] + 1
                queue.append(nxt)
    return levels


def _walk_down(source, adj):
    """从 source 向下做简单路径 DFS，返回 (paths, cycles, clipped)。

    当前路径上再次出现节点即判定为环路：记录成 ``... -> node (cycle)``，
    不再继续深入。路径数量和展开次数有上限，触顶时 ``clipped`` 为 True。
    """
    paths = defaultdict(list)
    cycles = []
    clipped = False
    expansions = 0
    stack = [(source, (source,))]

    while stack:
        node, chain = stack.pop()
        for nxt in adj.get(node, ()):
            if nxt in chain:
                cycles.append(_fmt_chain(list(chain) + [nxt]) + " (cycle)")
                continue
            if len(chain) >= _MAX_DEPTH:
                clipped = True
                continue

            expansions += 1
            if expansions > _MAX_EXPANSIONS:
                clipped = True
                return paths, cycles, clipped

            new_chain = chain + (nxt,)
            if len(paths[nxt]) < _MAX_PATHS_PER_NODE:
                paths[nxt].append(_fmt_chain(list(new_chain)))
            else:
                clipped = True
            stack.append((nxt, new_chain))

    return paths, cycles, clipped


def _walk_up(node, rev):
    """向上收集根到 node 的路径，用于 ``paths(node)`` 口径。"""
    out = []
    cycles = []
    clipped = False
    expansions = 0
    stack = [(node, (node,))]

    while stack:
        cur, chain = stack.pop()
        parents = rev.get(cur, ())
        if not parents:
            out.append(_fmt_chain(list(reversed(chain))))
            continue
        for parent in parents:
            if parent in chain:
                # chain 是 (起点, 其父, 祖父, ..., 当前节点)，相邻两项间方向为
                # chain[k+1] -> chain[k]。环边为 parent(=chain[i]) -> 当前节点。
                # 沿下行方向绕一圈：parent -> 当前节点 -> ... -> chain[i+1] -> parent。
                i = chain.index(parent)
                if i == len(chain) - 1:
                    # 自环：parent 就是当前节点，绕一段即可。
                    loop = [parent, parent]
                else:
                    loop = (
                        [parent, chain[-1]]
                        + list(reversed(chain[i + 1 : -1]))
                        + [parent]
                    )
                cycles.append(_fmt_chain(loop) + " (cycle)")
                continue
            if len(chain) >= _MAX_DEPTH:
                clipped = True
                continue

            expansions += 1
            if expansions > _MAX_EXPANSIONS:
                clipped = True
                return out, cycles, clipped
            stack.append((parent, chain + (parent,)))

    return out, cycles, clipped


# ---------------------------------------------------------------------------
# 影响面计算
# ---------------------------------------------------------------------------


def _build_table_impact(table):
    adj = _table_adj()
    levels = _levels(table, adj)
    paths, cycles, clipped = _walk_down(table, adj)

    details = []
    for node in levels:
        if node == table:  # 源节点不算自己的下游
            continue
        details.append(
            {
                "table": node,
                "level": levels[node],
                "paths": _sort_paths(paths.get(node, ())),
            }
        )
    details.sort(key=lambda item: (item["level"], item["table"]))

    return ImpactResult(
        [item["table"] for item in details],
        details=details,
        cycles=sorted(set(cycles)),
        clipped=clipped,
    )


def _build_field_impact(table, field):
    node = (table, field)
    field_adj = _field_adj()

    if not field_adj.get(node):
        # 兼容旧口径：字段没有任何字段级出边时，退回表级下游。
        result = _build_table_impact(table)
        result.field_fallback = True
        return result

    levels = _levels(node, field_adj)
    paths, cycles, clipped = _walk_down(node, field_adj)

    field_details = []
    for dst in levels:
        if dst == node:
            continue
        field_details.append(
            {
                "table": dst[0],
                "field": dst[1],
                "level": levels[dst],
                "paths": _sort_paths(paths.get(dst, ())),
            }
        )
    field_details.sort(key=lambda item: (item["level"], item["table"], item["field"]))

    # 按表汇总：最短层级 + 所有命中路径。
    table_details = {}
    for item in field_details:
        entry = table_details.setdefault(
            item["table"], {"table": item["table"], "level": item["level"], "paths": []}
        )
        entry["level"] = min(entry["level"], item["level"])
        entry["paths"].extend(item["paths"])
    details = []
    for table_name in sorted(
        table_details, key=lambda name: (table_details[name]["level"], name)
    ):
        entry = table_details[table_name]
        details.append(
            {
                "table": table_name,
                "level": entry["level"],
                "paths": _sort_paths(entry["paths"]),
            }
        )

    return ImpactResult(
        [item["table"] for item in details],
        details=details,
        fields=field_details,
        cycles=sorted(set(cycles)),
        clipped=clipped,
    )


def _clone_impact(result, *, cache_hit):
    return ImpactResult(
        list(result),
        details=[dict(item, paths=list(item["paths"])) for item in result.details],
        fields=[dict(item, paths=list(item["paths"])) for item in result.fields],
        cycles=list(result.cycles),
        clipped=result.clipped,
        cache_hit=cache_hit,
        field_fallback=result.field_fallback,
    )


def _cache_get(key, kind):
    if key in _cache:
        _stats["hits"] += 1
        _kind_stats[kind]["hits"] += 1
        return _cache[key], True
    _stats["misses"] += 1
    _kind_stats[kind]["misses"] += 1
    return None, False


# ---------------------------------------------------------------------------
# 对外查询
# ---------------------------------------------------------------------------


def impact(table, field=None):
    """查询 table（或 table.field）的多跳下游影响面。

    返回 ``ImpactResult``：内容为按影响程度排序的下游表名，可直接与
    ``["dwd", ...]`` 比较；``details`` / ``fields`` / ``cycles`` /
    ``cache_hit`` 提供层级和命中路径等明细。
    """
    key = ("impact", table, field)
    cached, hit = _cache_get(key, "impact")
    if hit:
        return _clone_impact(cached, cache_hit=True)

    result = _build_field_impact(table, field) if field is not None else _build_table_impact(table)
    _cache[key] = _clone_impact(result, cache_hit=False)
    return result


def _as_field_node(node):
    if isinstance(node, tuple) and len(node) == 2:
        return node
    if isinstance(node, str) and "." in node:
        table, field = node.rsplit(".", 1)
        return (table, field)
    return None


def paths(node):
    """返回根节点到 node 的简单血缘路径（字符串列表）。

    * 表节点：``paths("raw")``；作为根且无上游时返回 ``["raw"]``。
    * 字段节点：``paths(("raw", "id"))`` 或 ``paths("raw.id")``。
    * 环路会截断并记录到 ``.cycles``。
    """
    field_node = _as_field_node(node)
    is_field = field_node is not None
    key_node = field_node if is_field else node
    key = ("paths", key_node)

    cached, hit = _cache_get(key, "paths")
    if hit:
        return PathResult(list(cached), cycles=list(cached.cycles), clipped=cached.clipped, cache_hit=True)

    if is_field:
        adj, rev = _field_adj(), _field_rev()
    else:
        adj, rev = _table_adj(), _table_rev()
    found, cycles, clipped = _walk_up(key_node, rev)
    if not found:
        # 整条上游都处在环里：返回从环路里截断出来的路径，不做伪造成 "node"。
        found = list(cycles)
    result = PathResult(
        _sort_paths(found),
        cycles=sorted(set(cycles)),
        clipped=clipped,
        cache_hit=False,
    )
    _cache[key] = PathResult(list(result), cycles=list(result.cycles), clipped=result.clipped)
    return result
