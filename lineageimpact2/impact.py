"""血缘影响分析对外入口。

默认使用模块级的 :class:`~lineageimpact2.lineage.Lineage` 图，方便脚本直接
``add_edge`` 后查询；需要多图隔离时自行 ``Lineage()``。
"""

from __future__ import annotations

from .lineage import (
    DEFAULT_DECAY,
    DEFAULT_MAX_PATHS,
    DIRECT,
    INDIRECT,
    MAX_DEPTH,
    Lineage,
    export,
    replay,
    save,
)

__all__ = [
    "Lineage",
    "DIRECT",
    "INDIRECT",
    "MAX_DEPTH",
    "DEFAULT_DECAY",
    "DEFAULT_MAX_PATHS",
    "graph",
    "reset",
    "add_table",
    "add_field",
    "add_edge",
    "add_table_edge",
    "remove_edge",
    "rename_table",
    "rename_field",
    "set_edge_weight",
    "impact",
    "paths",
    "verify",
    "export",
    "save",
    "replay",
]

_default = Lineage()


def graph() -> Lineage:
    """返回默认图。"""
    return _default


def reset() -> Lineage:
    """清空默认图并返回它（测试或重新装载血缘时用）。"""
    global _default
    _default = Lineage()
    return _default


def add_table(name: str) -> str:
    return _default.add_table(name)


def add_field(table, field: str | None = None) -> str:
    return _default.add_field(table, field)


def add_edge(source, target, weight: float = 1.0) -> None:
    _default.add_edge(source, target, weight)


def add_table_edge(source, target, weight: float = 1.0) -> None:
    _default.add_table_edge(source, target, weight)


def remove_edge(source, target) -> bool:
    return _default.remove_edge(source, target)


def rename_table(old: str, new: str) -> None:
    _default.rename_table(old, new)


def rename_field(ref, new: str) -> str:
    return _default.rename_field(ref, new)


def set_edge_weight(source, target, weight: float) -> None:
    _default.set_edge_weight(source, target, weight)


def impact(
    table,
    field: str | None = None,
    *,
    max_depth: int = MAX_DEPTH,
    max_paths: int = DEFAULT_MAX_PATHS,
    decay: float = DEFAULT_DECAY,
    refresh: bool = False,
) -> dict:
    """在默认图上做影响分析。

    ``impact("ods.orders")`` 为表级分析；``impact("ods.orders", "id")`` 为字段级。
    也可直接传 ``"ods.orders.id"``（字段级）或 ``("ods.orders", "id")``。

    返回字段：

    ``downstream``            下游表名（去重、排序）
    ``downstream_fields``     下游字段全名（字段级分析时）
    ``impacts``               每个受影响节点的表、字段、层级、路径与得分，
                              按影响程度（得分）降序，同分按层级升序
    ``direct_impacts``        level == 1 的影响
    ``indirect_impacts``      level > 1 的影响
    ``paths`` / ``path_scores`` / ``path_levels``
                              按得分排序的路径及其得分、跳数
    ``cycles``                检测到的环（已截断，不会再进入）
    ``truncated`` / ``truncation``
                              是否因环 / 深度 / 条数上限被截断及原因
    ``breaks``                断点：起始节点缺失，或遇到表级血缘（字段映射未知）
    ``missing``               起始节点是否不在图中
    ``cache``                 是否命中缓存及对应图版本
    """
    return _default.impact(
        table, field, max_depth=max_depth, max_paths=max_paths,
        decay=decay, refresh=refresh,
    )


def paths(
    node,
    *,
    max_depth: int = MAX_DEPTH,
    max_paths: int = DEFAULT_MAX_PATHS,
    decay: float = DEFAULT_DECAY,
    refresh: bool = False,
) -> dict:
    """在默认图上查询某节点的影响路径。"""
    return _default.paths(
        node, max_depth=max_depth, max_paths=max_paths, decay=decay, refresh=refresh
    )


def verify(table, field: str | None, expected, **kwargs) -> dict:
    """核对计算结果与期望的下游集合是否一致。"""
    return _default.verify(table, field, expected, **kwargs)
