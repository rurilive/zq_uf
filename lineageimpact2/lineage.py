"""字段级血缘图。

节点用内部 id（``t1`` / ``f2`` …）标识，显示名随时可以改。改名只更新显示名并
登记旧名别名，旧名字仍然解析到同一个节点，所以不会因为改名产生重复节点。

图每次真实变更（新增节点 / 边、改名、改权重、删边）都会让 ``version`` 加一
并清空查询缓存，保证血缘变化后缓存失效。

公开入口见 :mod:`lineageimpact2.impact`（使用默认图），本模块的 :class:`Lineage`
支持多图隔离。
"""

from __future__ import annotations

import copy
import itertools
import json
import math

#: 单次查询允许的最大跳数，避免异常数据造成过深递归。
MAX_DEPTH = 64
#: 每多一跳的衰减系数，路径得分 = 边权重连乘 * decay^(跳数-1)。
DEFAULT_DECAY = 0.5
#: 单次查询最多返回多少条路径（按得分排序后截断）。
DEFAULT_MAX_PATHS = 200

DIRECT = "direct"
INDIRECT = "indirect"


class Lineage:
    """有向血缘图，字段边连接 ``(表, 字段) -> (表, 字段)``。

    字段用 ``"表.字段"`` 字符串描述；表名本身可以带点（如 ``ods.orders``），
    以最后一个点切分。表名 / 字段名有歧义时请传 ``(表, 字段)`` 元组。

    表级边（:meth:`add_table_edge`）表示“只知道表依赖、不知道字段映射”的血缘。
    字段级查询走到表级边时把下游标成 ``partial`` 并给出断点。
    """

    def __init__(self) -> None:
        self._version = 0
        self._seq = itertools.count(1)
        self._cache: dict = {}
        self._hits = 0
        self._misses = 0
        self._events: list[dict] = []

        self._table_name: dict[str, str] = {}
        self._table_alias: dict[str, str] = {}

        self._field_name: dict[str, str] = {}
        self._field_table: dict[str, str] = {}
        self._field_alias: dict[tuple[str, str], str] = {}
        self._qualified: dict[str, str] = {}

        self._fields_of: dict[str, list[str]] = {}
        self._out_fields: dict[str, list[dict]] = {}
        self._out_tables: dict[str, list[dict]] = {}

    # ================================================================ 元信息

    @property
    def version(self) -> int:
        return self._version

    def table_count(self) -> int:
        return len(self._table_name)

    def field_count(self) -> int:
        return len(self._field_name)

    def node_count(self) -> int:
        return self.table_count() + self.field_count()

    def table_name(self, table_id: str) -> str:
        return self._table_name[table_id]

    def field_name(self, field_id: str) -> str:
        return self._field_name[field_id]

    def qualified(self, field_id: str) -> str:
        return f"{self._table_name[self._field_table[field_id]]}.{self._field_name[field_id]}"

    def table_id(self, name: str) -> str | None:
        return self._table_alias.get(name)

    def field_id(self, ref) -> str | None:
        """解析字段引用（``"t.f"``、``(t, f)`` 或内部 id），解析不到返回 None。"""
        if isinstance(ref, str) and ref in self._field_name:
            return ref
        try:
            return self._resolve_field(ref, create=False)[0]
        except (KeyError, ValueError):
            return None

    def cache_info(self) -> dict:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._cache),
            "version": self._version,
        }

    def clear_cache(self) -> None:
        self._cache.clear()

    # ================================================================ 变更

    def _touch(self) -> None:
        self._version += 1
        self._cache.clear()

    def _ensure_table(self, name: str) -> str:
        tid = self._table_alias.get(name)
        if tid is not None:
            return tid
        tid = f"t{next(self._seq)}"
        self._table_name[tid] = name
        self._table_alias[name] = tid
        self._fields_of[tid] = []
        return tid

    def _ensure_field(self, table: str, field: str) -> str:
        tid = self._ensure_table(table)
        fid = self._field_alias.get((tid, field))
        if fid is not None:
            return fid
        fid = f"f{next(self._seq)}"
        self._field_name[fid] = field
        self._field_table[fid] = tid
        self._field_alias[(tid, field)] = fid
        self._qualified[f"{table}.{field}"] = fid
        self._fields_of[tid].append(fid)
        return fid

    def add_table(self, name: str) -> str:
        """登记一张表（已存在则返回原 id，不产生重复节点）。"""
        if not name:
            raise ValueError("表名不能为空")
        existed = str(name) in self._table_alias
        tid = self._ensure_table(str(name))
        if not existed:
            self._events.append({"op": "add_table", "name": str(name)})
            self._touch()
        return tid

    def add_field(self, table, field: str | None = None) -> str:
        """登记字段，支持 ``add_field("t", "f")`` 与 ``add_field("t.f")``。"""
        if field is None:
            table, field = self._split_pair(table)
        table, field = str(table), str(field)
        tid = self._table_alias.get(table)
        existed = tid is not None and (tid, field) in self._field_alias
        fid = self._ensure_field(table, field)
        if not existed:
            self._events.append({"op": "add_field", "table": table, "field": field})
            self._touch()
        return fid

    def add_edge(self, source, target, weight: float = 1.0) -> None:
        """新增字段级血缘边；端点未登记时自动创建。重复添加会更新权重。"""
        _check_weight(weight)
        st, sf = self._split_pair(source)
        tt, tf = self._split_pair(target)
        sid = self._ensure_field(st, sf)
        tid = self._ensure_field(tt, tf)
        self._upsert(self._out_fields.setdefault(sid, []), tid, float(weight))
        self._events.append(
            {"op": "add_edge", "src": f"{st}.{sf}", "dst": f"{tt}.{tf}",
             "weight": float(weight)}
        )
        self._touch()

    def add_table_edge(self, source, target, weight: float = 1.0) -> None:
        """新增表级血缘边（只知道表依赖，字段映射未知）。

        ``source`` / ``target`` 就是表名本身，可以带点（``dwd.order_fact``）；
        不要传字段引用。
        """
        _check_weight(weight)
        st, tt = _as_table_name(source), _as_table_name(target)
        sid = self._ensure_table(st)
        tid = self._ensure_table(tt)
        if sid == tid:
            raise ValueError("表级边不能自环")
        self._upsert(self._out_tables.setdefault(sid, []), tid, float(weight))
        self._events.append(
            {"op": "add_table_edge", "src": st, "dst": tt, "weight": float(weight)}
        )
        self._touch()

    def remove_edge(self, source, target) -> bool:
        try:
            sid = self._resolve_field(source, create=False)[0]
            tid = self._resolve_field(target, create=False)[0]
        except (KeyError, ValueError):
            return False
        if not self._drop(self._out_fields.get(sid, []), tid):
            return False
        self._events.append(
            {"op": "remove_edge", "src": self.qualified(sid), "dst": self.qualified(tid)}
        )
        self._touch()
        return True

    def remove_table_edge(self, source, target) -> bool:
        sid = self._table_alias.get(_as_table_name(source))
        tid = self._table_alias.get(_as_table_name(target))
        if sid is None or tid is None or not self._drop(self._out_tables.get(sid, []), tid):
            return False
        self._events.append(
            {"op": "remove_table_edge",
             "src": self._table_name[sid], "dst": self._table_name[tid]}
        )
        self._touch()
        return True

    def rename_table(self, old: str, new: str) -> None:
        """改名：保留节点 id，旧名作为别名继续可解析，不产生重复节点。"""
        tid = self._table_alias.get(str(old))
        if tid is None:
            raise KeyError(f"未知表: {old}")
        if new in self._table_alias and self._table_alias[new] != tid:
            raise ValueError(f"表名已被占用: {new}")
        self._table_name[tid] = new
        self._table_alias[new] = tid
        for fid in self._fields_of[tid]:
            self._qualified[f"{new}.{self._field_name[fid]}"] = fid
        self._events.append({"op": "rename_table", "old": str(old), "new": new})
        self._touch()

    def rename_field(self, ref, new: str) -> str:
        """改名：保留节点 id，旧名作为别名继续可解析，不产生重复节点。"""
        fid, tid = self._resolve_field(ref, create=False)
        if new in self._field_alias and self._field_alias[(tid, new)] != fid:
            raise ValueError(f"字段名已被占用: {new}")
        old_qualified = self.qualified(fid)
        self._field_name[fid] = new
        self._field_alias[(tid, new)] = fid
        self._qualified[f"{self._table_name[tid]}.{new}"] = fid
        self._events.append(
            {"op": "rename_field", "table": self._table_name[tid],
             "old": old_qualified, "new": new}
        )
        self._touch()
        return fid

    def set_edge_weight(self, source, target, weight: float) -> None:
        """调整边权重；权重用于影响程度排序。"""
        _check_weight(weight)
        sid = self._resolve_field(source, create=False)[0]
        tid = self._resolve_field(target, create=False)[0]
        edges = self._out_fields.get(sid, [])
        for e in edges:
            if e["to"] == tid:
                e["weight"] = float(weight)
                self._events.append(
                    {"op": "set_weight", "src": self.qualified(sid),
                     "dst": self.qualified(tid), "weight": float(weight)}
                )
                self._touch()
                return
        raise KeyError("血缘边不存在")

    # ================================================================ 查询

    def impact(
        self,
        table,
        field: str | None = None,
        *,
        max_depth: int = MAX_DEPTH,
        max_paths: int = DEFAULT_MAX_PATHS,
        decay: float = DEFAULT_DECAY,
        refresh: bool = False,
    ) -> dict:
        """影响分析。

        ``field`` 为空时做表级分析（字段边按表折叠），否则做字段级分析。
        返回下游表、字段、层级（level）与路径，按影响程度从高到低排序；
        直接 / 间接影响分别标注；环路截断并记录；断链给出断点。
        """
        _check_limits(max_depth, max_paths, decay)

        if isinstance(table, tuple):
            table, tuple_field = table[0], (table[1] if len(table) > 1 else None)
            if field is None:
                field = tuple_field

        key, missing_table, missing_field = self._resolve_query(table, field)
        if key is None:
            return self._empty_result(
                missing_table, missing_field, max_depth, max_paths, decay
            )

        ck = ("impact", key, max_depth, max_paths, round(float(decay), 12))
        if not refresh and ck in self._cache:
            self._hits += 1
            result = copy.deepcopy(self._cache[ck])
            result["cache"] = {"hit": True, "version": self._version}
            return result
        self._misses += 1

        result = self._analyse(key, max_depth, max_paths, decay)
        result["cache"] = {"hit": False, "version": self._version}
        self._cache[ck] = copy.deepcopy(result)
        return result

    def paths(
        self,
        node,
        *,
        max_depth: int = MAX_DEPTH,
        max_paths: int = DEFAULT_MAX_PATHS,
        decay: float = DEFAULT_DECAY,
        refresh: bool = False,
    ) -> dict:
        """单独返回某节点的影响路径（含层级、得分、环与断点）。"""
        if isinstance(node, tuple):
            table, field = node[0], (node[1] if len(node) > 1 else None)
        elif isinstance(node, str) and "." in node and node not in self._table_alias:
            table, field = self._split_pair(node)
        else:
            table, field = node, None
        full = self.impact(
            table, field, max_depth=max_depth, max_paths=max_paths,
            decay=decay, refresh=refresh,
        )
        return {
            "node": full["query"]["qualified"],
            "table": full["query"]["table"],
            "field": full["query"]["field"],
            "paths": full["paths"],
            "path_scores": full["path_scores"],
            "path_levels": full["path_levels"],
            "cycles": full["cycles"],
            "breaks": full["breaks"],
            "truncated": full["truncated"],
            "truncation": full["truncation"],
            "missing": full["missing"],
            "version": full["version"],
            "cache": full["cache"],
            "impacts": full["impacts"],
        }

    def verify(self, table, field: str | None, expected, **kwargs) -> dict:
        """核对多跳结果与期望是否一致（在一个核心表上验证用）。"""
        result = self.impact(table, field, **kwargs)
        computed = {e["qualified"] for e in result["impacts"]}
        want = {_norm_ref(x) for x in expected}
        return {
            "ok": computed == want,
            "missing": sorted(want - computed),
            "unexpected": sorted(computed - want),
            "computed": sorted(computed),
            "result": result,
        }

    # ------------------------------------------------------------ 遍历实现

    def _analyse(self, start, max_depth: int, max_paths: int, decay: float) -> dict:
        out_fn = self._field_out if start[0] == "f" else self._table_out

        arrivals: list[dict] = []
        cycles: list[dict] = []
        breaks: list[dict] = []
        truncation: list[str] = []
        cycle_keys: set[tuple[str, str]] = set()
        pushes = 0
        max_pushes = max(1000, max_paths * max(8, max_depth))

        stack = [{
            "key": start,
            "keys": (start,),
            "labels": [self._label(start)],
            "prod": 1.0,
            "partial": False,
        }]
        while stack:
            cur = stack.pop()
            depth = len(cur["keys"]) - 1
            edges = out_fn(cur["key"])
            if not edges:
                continue
            if depth >= max_depth:
                truncation.append("depth")
                continue

            for dst, weight, partial, reason in _sorted_edges(edges, self._label):
                if dst in cur["keys"]:
                    ck = (self._label(cur["key"]), self._label(dst))
                    if ck not in cycle_keys:
                        cycle_keys.add(ck)
                        cycles.append({
                            "from": ck[0],
                            "to": ck[1],
                            "path": list(cur["labels"]) + [self._label(dst)],
                        })
                        truncation.append("cycle")
                    continue

                pushes += 1
                if pushes > max_pushes:
                    truncation.append("limit")
                    stack.clear()
                    break

                prod = cur["prod"] * weight
                level = depth + 1
                score = prod * math.pow(decay, level - 1)
                labels = list(cur["labels"]) + [self._label(dst)]
                keys = cur["keys"] + (dst,)

                is_partial = cur["partial"] or partial
                point = None
                if is_partial:
                    point = {
                        "from": cur["labels"][-1],
                        "to": self._label(dst),
                        "table": self._table_of(dst),
                        "field": self._field_of(dst),
                        "reason": reason if partial else "上游字段映射未知",
                        "path": list(labels),
                    }
                    breaks.append(point)

                arrivals.append({
                    "key": dst,
                    "keys": keys,
                    "labels": labels,
                    "score": score,
                    "level": level,
                    "partial": is_partial,
                    "breakpoint": point,
                })
                # 断在表级：不再往下钻，否则会凭空断言某字段受影响。
                if is_partial:
                    continue
                stack.append({
                    "key": dst,
                    "keys": keys,
                    "labels": labels,
                    "prod": prod,
                    "partial": False,
                })

        impacts = self._dedupe(arrivals)
        impacts.sort(key=_impact_sort_key)

        paths: list[list[str]] = []
        scores: list[float] = []
        levels: list[int] = []
        seen_paths: set[tuple[str, ...]] = set()
        ordered = sorted(arrivals, key=lambda a: (-a["score"], a["level"], a["labels"]))
        for a in ordered:
            p = tuple(a["labels"])
            if p in seen_paths:
                continue
            if len(paths) >= max_paths:
                truncation.append("paths")
                break
            seen_paths.add(p)
            paths.append(list(p))
            scores.append(_round(a["score"]))
            levels.append(a["level"])

        direct = [e for e in impacts if e["level"] == 1]
        indirect = [e for e in impacts if e["level"] > 1]
        cycles.sort(key=lambda c: (len(c["path"]), c["from"], c["to"]))
        breaks.sort(key=lambda b: (b["path"], b["table"], b["from"]))
        unique_breaks = _dedupe_breaks(breaks)

        return {
            "query": {
                "table": self._table_of(start),
                "field": self._field_of(start),
                "qualified": self._label(start),
            },
            "table": self._table_of(start),
            "field": self._field_of(start),
            "version": self._version,
            "depth": max((e["level"] for e in impacts), default=0),
            "direct_count": len(direct),
            "indirect_count": len(indirect),
            "total": len(impacts),
            "downstream": sorted({e["table"] for e in impacts}),
            "downstream_fields": sorted(
                e["qualified"] for e in impacts if e["field"] is not None
            ),
            "impacts": impacts,
            "direct_impacts": direct,
            "indirect_impacts": indirect,
            "paths": paths,
            "path_scores": scores,
            "path_levels": levels,
            "cycles": cycles,
            "breaks": unique_breaks,
            "truncated": bool(truncation),
            "truncation": sorted(set(truncation)),
            "limits": {"max_depth": max_depth, "max_paths": max_paths, "decay": decay},
            "missing": False,
        }

    def _empty_result(self, table, field, max_depth, max_paths, decay) -> dict:
        label = table if field is None else f"{table}.{field}"
        point = {
            "from": None,
            "to": label,
            "table": table,
            "field": field,
            "reason": "起始节点不在血缘图中",
            "path": [label],
        }
        return {
            "query": {"table": table, "field": field, "qualified": label},
            "table": table,
            "field": field,
            "version": self._version,
            "depth": 0,
            "direct_count": 0,
            "indirect_count": 0,
            "total": 0,
            "downstream": [],
            "downstream_fields": [],
            "impacts": [],
            "direct_impacts": [],
            "indirect_impacts": [],
            "paths": [],
            "path_scores": [],
            "path_levels": [],
            "cycles": [],
            "breaks": [point],
            "truncated": False,
            "truncation": [],
            "limits": {"max_depth": max_depth, "max_paths": max_paths, "decay": decay},
            "missing": True,
            "cache": {"hit": False, "version": self._version},
        }

    def _dedupe(self, arrivals: list[dict]) -> list[dict]:
        """同一节点保留影响最大（得分最高）的一条路径。"""
        best: dict[tuple, dict] = {}
        for a in arrivals:
            cur = best.get(a["key"])
            if cur is None or (a["score"], -a["level"]) > (cur["score"], -cur["level"]):
                best[a["key"]] = a
        out = []
        for key, a in best.items():
            table = self._table_of(key)
            field = self._field_of(key)
            out.append({
                "table": table,
                "field": field,
                "qualified": self._label(key),
                "level": a["level"],
                "hops": a["level"],
                "direct": a["level"] == 1,
                "indirect": a["level"] > 1,
                "status": DIRECT if a["level"] == 1 else INDIRECT,
                "impact": _round(a["score"]),
                "score": _round(a["score"]),
                "path": list(a["labels"]),
                "path_tables": [self._table_of(k) for k in a["keys"]],
                "partial": a["partial"],
                "breakpoint": a["breakpoint"],
            })
        return out

    def _field_out(self, key) -> list[tuple]:
        if key[0] != "f":
            return []
        fid = key[1]
        edges = [
            (("f", e["to"]), e["weight"], False, None)
            for e in self._out_fields.get(fid, ())
        ]
        tid = self._field_table[fid]
        for e in self._out_tables.get(tid, ()):
            edges.append((("t", e["to"]), e["weight"], True, "表级血缘，字段映射未知"))
        return edges

    def _table_out(self, key) -> list[tuple]:
        if key[0] != "t":
            return []
        tid = key[1]
        edges = [
            (("t", e["to"]), e["weight"], False, None)
            for e in self._out_tables.get(tid, ())
        ]
        for fid in self._fields_of.get(tid, ()):
            for e in self._out_fields.get(fid, ()):
                edges.append((("t", self._field_table[e["to"]]), e["weight"], False, None))
        return edges

    def _label(self, key) -> str:
        return self.qualified(key[1]) if key[0] == "f" else self._table_name[key[1]]

    def _table_of(self, key) -> str:
        if key[0] == "f":
            return self._table_name[self._field_table[key[1]]]
        return self._table_name[key[1]]

    def _field_of(self, key) -> str | None:
        return None if key[0] == "t" else self._field_name[key[1]]

    # ================================================================ 导出 / 回放

    def to_dict(self) -> dict:
        """导出整张图（含别名与事件日志），可持久化后用 :meth:`replay` 还原。"""
        return {
            "version": self._version,
            "tables": [
                {"id": tid, "name": name,
                 "aliases": self._aliases(self._table_alias, tid)}
                for tid, name in self._table_name.items()
            ],
            "fields": [
                {"id": fid, "table": self._table_name[self._field_table[fid]], "name": name,
                 "aliases": self._aliases(self._qualified, fid)}
                for fid, name in self._field_name.items()
            ],
            "field_edges": [
                {"src": self.qualified(fid), "dst": self.qualified(e["to"]),
                 "weight": e["weight"]}
                for fid, es in self._out_fields.items() for e in es
            ],
            "table_edges": [
                {"src": self._table_name[tid], "dst": self._table_name[e["to"]],
                 "weight": e["weight"]}
                for tid, es in self._out_tables.items() for e in es
            ],
            "events": copy.deepcopy(self._events),
        }

    def events(self) -> list[dict]:
        """按发生顺序返回变更事件，可直接交给 :func:`replay`。"""
        return copy.deepcopy(self._events)

    def replay(self, events) -> "Lineage":
        """在当前图上按顺序重放事件。"""
        _apply_events(self, events)
        return self

    @classmethod
    def from_events(cls, events) -> "Lineage":
        """用事件日志构建同一张图（回放）。"""
        graph = cls()
        _apply_events(graph, events)
        return graph

    @staticmethod
    def _aliases(alias_map: dict, node_id) -> list[str]:
        return sorted(str(k) for k, v in alias_map.items() if v == node_id)

    # ------------------------------------------------------------ 内部工具

    def _split_pair(self, ref) -> tuple[str, str]:
        if isinstance(ref, tuple):
            if len(ref) != 2:
                raise ValueError("字段引用应为 (表, 字段) 二元组")
            return str(ref[0]), str(ref[1])
        if not isinstance(ref, str):
            raise ValueError("字段引用应为 '表.字段' 字符串或 (表, 字段) 元组")
        if ref in self._qualified:
            fid = self._qualified[ref]
            return self._table_name[self._field_table[fid]], self._field_name[fid]
        known = self._split_known(ref)
        if known is not None:
            return known
        if "." not in ref:
            raise ValueError(f"字段引用缺少表名: {ref!r}，请用 '表.字段'")
        table, field = ref.rsplit(".", 1)
        if not table or not field:
            raise ValueError(f"非法字段引用: {ref!r}")
        return table, field

    def _resolve_query(self, table, field) -> tuple[tuple | None, str, str | None]:
        """把查询参数解析成图节点键；解析不到时返回 (None, 表名, 字段名)。"""
        if field is None and isinstance(table, str):
            # 已知表名优先（表级查询）；其次整串当字段全名。
            tid = self._table_alias.get(table)
            if tid is not None:
                return ("t", tid), table, None
            if table in self._qualified:
                fid = self._qualified[table]
                return ("f", fid), self._table_name[self._field_table[fid]], \
                    self._field_name[fid]
            if table in self._field_name:
                fid = table
                return ("f", fid), self._table_name[self._field_table[fid]], \
                    self._field_name[fid]
            split = self._split_known(table)
            if split is not None:
                fid = self._field_alias[(self._table_alias[split[0]], split[1])]
                return ("f", fid), split[0], split[1]
            return None, table, None

        table, field = str(table), str(field)
        try:
            return ("f", self._resolve_field((table, field), create=False)[0]), table, field
        except (KeyError, ValueError):
            return None, table, field

    def _split_known(self, ref: str) -> tuple[str, str] | None:
        """``"表.字段"`` 且表名可含点：取最长已知表名前缀切分。"""
        best = None
        for tname in self._table_alias:
            prefix = tname + "."
            if ref.startswith(prefix) and len(ref) > len(prefix):
                if best is None or len(tname) > len(best):
                    best = tname
        if best is None:
            return None
        field = ref[len(best) + 1:]
        if (self._table_alias[best], field) not in self._field_alias:
            return None
        return best, field

    def _resolve_field(self, ref, create: bool) -> tuple[str, str]:
        """返回 (字段 id, 表 id)。"""
        if isinstance(ref, str) and ref in self._field_name:
            return ref, self._field_table[ref]
        table, field = self._split_pair(ref)
        tid = self._table_alias.get(table)
        if tid is None:
            if not create:
                raise KeyError(f"未知表: {table}")
            tid = self._ensure_table(table)
        fid = self._field_alias.get((tid, field))
        if fid is None:
            if not create:
                raise KeyError(f"未知字段: {table}.{field}")
            fid = self._ensure_field(table, field)
        return fid, tid

    @staticmethod
    def _upsert(edges: list[dict], dst: str, weight: float) -> bool:
        for e in edges:
            if e["to"] == dst:
                e["weight"] = weight
                return True
        edges.append({"to": dst, "weight": weight})
        return True

    @staticmethod
    def _drop(edges: list[dict], dst: str) -> bool:
        for i, e in enumerate(edges):
            if e["to"] == dst:
                del edges[i]
                return True
        return False


# ================================================================ 模块级工具


def _check_weight(weight) -> None:
    if not isinstance(weight, (int, float)) or isinstance(weight, bool):
        raise ValueError("权重必须是数字")
    if not math.isfinite(weight) or weight <= 0:
        raise ValueError("权重必须是正的有限数")


def _check_limits(max_depth: int, max_paths: int, decay: float) -> None:
    if not isinstance(max_depth, int) or max_depth < 1:
        raise ValueError("max_depth 必须是不小于 1 的整数")
    if not isinstance(max_paths, int) or max_paths < 1:
        raise ValueError("max_paths 必须是不小于 1 的整数")
    if decay <= 0 or not math.isfinite(decay):
        raise ValueError("decay 必须是正的有限数")


def _round(x: float) -> float:
    return round(float(x), 6)


def _impact_sort_key(e: dict) -> tuple:
    return (-e["impact"], e["level"], e["table"], "" if e["field"] is None else e["field"])


def _norm_ref(x) -> str:
    if isinstance(x, tuple):
        return f"{x[0]}.{x[1]}"
    return str(x)


def _as_table_name(ref) -> str:
    """表级引用 -> 表名：元组取首项，字符串原样返回（表名允许带点）。"""
    if isinstance(ref, tuple):
        return str(ref[0])
    return str(ref)


def _sorted_edges(edges, label_fn):
    return sorted(edges, key=lambda e: (-e[1], label_fn(e[0]), e[0]))


def _dedupe_breaks(breaks: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out = []
    for b in breaks:
        key = (tuple(b["path"]), b["table"], b["field"], b["reason"])
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def export(result: dict, fmt: str = "json") -> str:
    """把影响分析结果导出为 json 字符串或 csv 文本。"""
    fmt = fmt.lower()
    if fmt == "json":
        return json.dumps(result, ensure_ascii=False, indent=2)
    if fmt == "csv":
        rows = [["table", "field", "level", "status", "impact", "partial", "path"]]
        for e in result.get("impacts", []):
            rows.append([
                e["table"],
                "" if e["field"] is None else e["field"],
                str(e["level"]),
                e["status"],
                f"{e['impact']:.6f}",
                "1" if e["partial"] else "0",
                " -> ".join(e["path"]),
            ])
        for b in result.get("breaks", []):
            if b.get("from") is None:
                continue
            rows.append([
                b["table"],
                "" if b["field"] is None else b["field"],
                "",
                "break",
                "",
                "1",
                " -> ".join(b["path"]),
            ])
        return "\n".join(",".join(_csv_cell(c) for c in r) for r in rows)
    raise ValueError(f"不支持的导出格式: {fmt}")


def _csv_cell(value: str) -> str:
    if any(ch in value for ch in ', "\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


def save(result: dict, path: str, fmt: str | None = None) -> str:
    """把导出文本写入文件，返回路径。格式缺省按扩展名推断。"""
    if fmt is None:
        fmt = "csv" if str(path).lower().endswith(".csv") else "json"
    text = export(result, fmt)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")
    return path


def replay(events, graph: "Lineage | None" = None) -> "Lineage":
    """回放事件日志；不传 graph 时新建一个图。"""
    graph = Lineage() if graph is None else graph
    _apply_events(graph, events)
    return graph


def _apply_events(graph: Lineage, events) -> None:
    for ev in events:
        op = ev.get("op")
        if op == "add_table":
            graph.add_table(ev["name"])
        elif op == "add_field":
            graph.add_field(ev["table"], ev["field"])
        elif op == "add_edge":
            graph.add_edge(ev["src"], ev["dst"], ev.get("weight", 1.0))
        elif op == "add_table_edge":
            graph.add_table_edge(ev["src"], ev["dst"], ev.get("weight", 1.0))
        elif op == "remove_edge":
            graph.remove_edge(ev["src"], ev["dst"])
        elif op == "remove_table_edge":
            graph.remove_table_edge(ev["src"], ev["dst"])
        elif op == "rename_table":
            graph.rename_table(ev["old"], ev["new"])
        elif op == "rename_field":
            old = ev["old"]
            ref = old if "." in str(old) else (ev["table"], old)
            graph.rename_field(ref, ev["new"])
        elif op == "set_weight":
            graph.set_edge_weight(ev["src"], ev["dst"], ev["weight"])
        else:
            raise ValueError(f"未知事件: {op!r}")
