"""数据保留标签：沿血缘继承、豁免到期、按依赖分批级联删除。

对外主接口保持 ``tag(table, value)`` 和 ``apply(table)``。其余函数用于
描述血缘、豁免、租约以及读取审计/传播记录，均为纯标准库实现。

标签口径
--------
``keepN`` 表示保留 N 天，N 越小越紧。一个表的生效标签 = 自身直挂标签与
所有上游生效标签中最紧的那个。下游可以挂更紧的标签，但不能放宽：若下游
直挂标签比继承来的更松，生效标签仍取继承值，并在传播记录里标记 rejected。

级联口径
--------
``apply(table)`` 处理目标表及其全部下游。删除按依赖顺序进行：叶子先删、
目标表最后删；同一依赖深度算一个批次。活跃豁免和活跃租约会让对应表跳过；
一旦某个下游被跳过，它的上游也会被阻塞，避免删除维表后留下悬空事实表。
"""

from __future__ import annotations

import heapq
import re
from datetime import date, datetime, timedelta, timezone

# 直接标签：table -> tag（保持旧测试依赖的 _tags 结构）
_tags: dict[str, str] = {}
# 血缘：parent -> [child, ...]，parent 被下游依赖
_lineage: dict[str, list[str]] = {}
# 豁免：table -> 记录，记录含 expires_at / approver
_exemptions: dict[str, dict] = {}
# 租约：table -> 记录或 bool
_leases: dict[str, object] = {}
# 标签变更审计
_audit: list[dict] = []
# 当前传播记录（每次重算刷新，记录来源层）
_propagation: list[dict] = []
# 重算得到的生效标签及来源
_effective: dict[str, str] = {}
_sources: dict[str, tuple[str, int]] = {}

_RETENTION_RE = re.compile(r"keep(\d+)", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value) -> datetime:
    """把豁免/租约时间统一成带 UTC 时区的 datetime。"""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        dt = _now() + timedelta(days=value)
    else:
        raise TypeError("expiry must be datetime, date, ISO string or days from now")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _retention_days(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    match = _RETENTION_RE.fullmatch(text)
    if match:
        return int(match.group(1))
    if text.isdigit():
        return int(text)
    return None


def _tightness(value) -> float:
    """数值越小越紧；无法解析的标签按最松处理。"""
    days = _retention_days(value)
    return float("inf") if days is None else float(days)


def _iter_links():
    """兼容 dict(parent -> children) 与 [(parent, child), ...] 两种血缘写法。"""
    if hasattr(_lineage, "items"):
        for parent, children in _lineage.items():
            if isinstance(children, str):
                children = [children]
            for child in children:
                yield parent, child
    else:
        for parent, child in _lineage:
            yield parent, child


def _children(table: str) -> list[str]:
    return [child for parent, child in _iter_links() if parent == table]


def _graph() -> tuple[set[str], dict[str, set[str]]]:
    nodes = set(_tags)
    parents: dict[str, set[str]] = {}
    for parent, child in _iter_links():
        nodes.add(parent)
        nodes.add(child)
        parents.setdefault(child, set()).add(parent)
        parents.setdefault(parent, set())
    for node in nodes:
        parents.setdefault(node, set())
    return nodes, parents


def _topo_order() -> list[str]:
    nodes, parents = _graph()
    indegree = {node: len(parents[node]) for node in nodes}
    ready = [node for node in nodes if indegree[node] == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        node = heapq.heappop(ready)
        order.append(node)
        for child in _children(node):
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, child)
    if len(order) != len(nodes):
        raise ValueError("lineage contains a cycle")
    return order


def _recompute() -> None:
    """重算全图生效标签，并刷新传播记录（含来源层）。"""
    global _effective, _sources, _propagation
    order = _topo_order()
    _, parents = _graph()
    effective: dict[str, str] = {}
    sources: dict[str, tuple[str, int]] = {}
    records: list[dict] = []

    for node in order:
        inherited = None
        inherited_meta = None
        for parent in sorted(parents[node]):
            parent_tag = effective.get(parent)
            if parent_tag is None:
                continue
            if inherited is None or _tightness(parent_tag) < _tightness(inherited):
                inherited = parent_tag
                origin, layer = sources[parent]
                inherited_meta = (origin, layer + 1, parent)

        own = _tags.get(node)
        if own is None:
            if inherited is None:
                continue
            effective[node] = inherited
            origin, layer, via = inherited_meta
            sources[node] = (origin, layer)
            records.append(
                {
                    "table": node,
                    "tag": inherited,
                    "source": origin,
                    "via": via,
                    "layer": layer,
                }
            )
            continue

        if inherited is not None and _tightness(own) > _tightness(inherited):
            # 下游尝试放宽：保留继承来的更紧标签。
            effective[node] = inherited
            origin, layer, via = inherited_meta
            sources[node] = (origin, layer)
            records.append(
                {
                    "table": node,
                    "tag": inherited,
                    "source": origin,
                    "via": via,
                    "layer": layer,
                    "rejected": own,
                }
            )
        else:
            effective[node] = own
            sources[node] = (node, 0)

    _effective = effective
    _sources = sources
    _propagation = records


def reset() -> None:
    """清空全部模块状态，供测试和 demo 隔离使用。"""
    _tags.clear()
    _lineage.clear()
    _exemptions.clear()
    _leases.clear()
    _audit.clear()
    _effective.clear()
    _sources.clear()
    _propagation.clear()


def link(parent: str, *children: str) -> dict:
    """登记血缘：child 依赖 parent。删除时 child 先于 parent。"""
    global _lineage
    if not children:
        raise ValueError("link requires at least one child")
    if not isinstance(_lineage, dict):
        # 兼容外部直接把 _lineage 设成 [(parent, child), ...] 的写法。
        converted: dict[str, list[str]] = {}
        for existing_parent, existing_child in _lineage:
            converted.setdefault(existing_parent, []).append(existing_child)
        _lineage = converted
    known = _lineage.setdefault(parent, [])
    for child in children:
        if child not in known:
            known.append(child)
    return {"parent": parent, "children": list(children)}


def effective(table: str) -> str | None:
    """返回单表生效标签。"""
    _recompute()
    return _effective.get(table)


def effective_tags() -> dict[str, str]:
    """返回全图生效标签快照。"""
    _recompute()
    return dict(_effective)


def propagation() -> list[dict]:
    """返回当前标签传播记录；source 是标签来源表，layer 是来源层数。"""
    _recompute()
    return [dict(record) for record in _propagation]


def audit() -> list[dict]:
    """返回标签变更审计记录。"""
    return [dict(entry) for entry in _audit]


def tag(table: str, value: str) -> dict:
    """挂标签并沿血缘向下游传播。

    返回字段：table、tag（直挂值）、effective（本表生效值）、propagated
    （波及下游的传播记录）、audit（本次变更审计，未变更时为 None）。
    """
    previous = _tags.get(table)
    _tags[table] = value
    _recompute()

    entry = None
    if previous != value:
        entry = {
            "action": "tag",
            "table": table,
            "old": previous,
            "new": value,
            "at": _now().isoformat(),
        }
        _audit.append(entry)

    descendants = set(_descendants(table))
    # 含本表：本表挂的标签被上游否决时，同样需要在记录里体现。
    propagated = [
        dict(record)
        for record in _propagation
        if record["table"] in descendants or record["table"] == table
    ]
    return {
        "table": table,
        "tag": value,
        "effective": _effective.get(table),
        "propagated": propagated,
        "audit": dict(entry) if entry else None,
    }


def _descendants(table: str) -> list[str]:
    seen: set[str] = set()
    queue = [table]
    result: list[str] = []
    while queue:
        node = queue.pop(0)
        for child in _children(node):
            if child not in seen:
                seen.add(child)
                result.append(child)
                queue.append(child)
    return result


def exempt(table: str, until, approver: str, reason: str | None = None) -> dict:
    """登记有到期时间和审批人的豁免；到期后自动失效。"""
    if not approver or not str(approver).strip():
        raise ValueError("exempt requires a non-empty approver")
    record = {
        "table": table,
        "expires_at": _as_datetime(until).isoformat(),
        "approver": str(approver).strip(),
        "reason": reason,
        "created_at": _now().isoformat(),
    }
    _exemptions[table] = record
    return dict(record)


def _record_expiry(record: dict) -> datetime:
    for key in ("expires_at", "until", "expiry", "expires"):
        if key in record:
            return _as_datetime(record[key])
    raise ValueError("exemption record is missing its expiry time")


def _exemption_info(record: dict, now: datetime) -> dict:
    info = dict(record)
    expires = _record_expiry(record)
    info.setdefault("expires_at", expires.isoformat())
    info["status"] = "active" if expires > now else "expired"
    return info


def _active_exemption(table: str, now: datetime) -> dict | None:
    record = _exemptions.get(table)
    if not record:
        return None
    expires = _record_expiry(record)
    return record if expires > now else None


def lease(table: str, until=None, active: bool = True) -> dict | None:
    """登记或解除租约；活跃租约在级联删除时整表跳过。"""
    if isinstance(until, bool):
        active = until
        until = None
    if not active:
        _leases.pop(table, None)
        return None
    record = {
        "table": table,
        "until": _as_datetime(until).isoformat() if until is not None else None,
        "active": True,
    }
    _leases[table] = record
    return dict(record)


def release(table: str) -> None:
    """解除表租约。"""
    _leases.pop(table, None)


def _lease_info(table: str, now: datetime) -> dict | None:
    record = _leases.get(table)
    if record is None or record is False:
        return None
    if record is True:
        return {"table": table, "active": True, "until": None}
    if isinstance(record, dict):
        if not record.get("active", True):
            return None
        until = record.get("until")
        if until is not None and _as_datetime(until) <= now:
            return None
        return dict(record)
    # 兼容直接把到期时间放进 _leases 的写法。
    if _as_datetime(record) <= now:
        return None
    return {"table": table, "active": True, "until": _as_datetime(record).isoformat()}


def _depths(table: str) -> dict[str, int]:
    """目标表到各下游的最大血缘深度。"""
    depths = {table: 0}
    queue = [table]
    while queue:
        node = queue.pop(0)
        for child in _children(node):
            candidate = depths[node] + 1
            if child not in depths or candidate > depths[child]:
                depths[child] = candidate
                queue.append(child)
    return depths


def apply(table: str) -> dict:
    """按生效标签级联清理目标表及其下游。

    返回字段：tag（目标生效标签）、effective（子树生效标签）、exemptions
    （含 active/expired 状态）、dependency_order（叶子先的删除顺序）、
    batches（按依赖深度分出的删除批次）、skipped、deleted。
    """
    _recompute()
    now = _now()
    depths = _depths(table)
    subtree = set(depths)

    effective = {
        node: _effective[node]
        for node in subtree
        if node in _effective
    }
    exemptions = [
        _exemption_info(record, now)
        for node, record in sorted(_exemptions.items())
        if node in subtree
    ]

    # 叶子先、目标表最后；同层按名字稳定排序。
    order = sorted(
        (node for node in subtree if node in _effective),
        key=lambda node: (-depths[node], node),
    )

    retained: set[str] = set()
    skipped: list[dict] = []
    deleted: list[str] = []
    for node in order:
        skip = None
        exemption = _active_exemption(node, now)
        if exemption is not None:
            skip = {
                "table": node,
                "reason": "active exemption",
                "approver": exemption.get("approver"),
            }
        elif _lease_info(node, now) is not None:
            skip = {"table": node, "reason": "active lease"}
        else:
            children = [child for child in _children(node) if child in depths]
            blocked = next((child for child in children if child in retained), None)
            if blocked is not None:
                skip = {
                    "table": node,
                    "reason": "blocked by retained dependent",
                    "blocked_by": blocked,
                }
        if skip is not None:
            retained.add(node)
            skipped.append(skip)
        else:
            deleted.append(node)

    grouped: dict[int, list[str]] = {}
    for node in deleted:
        grouped.setdefault(depths[node], []).append(node)
    batches = []
    for index, depth in enumerate(sorted(grouped, reverse=True), start=1):
        tables = sorted(grouped[depth])
        batches.append(
            {"batch": index, "depth": depth, "tables": tables, "deleted": len(tables)}
        )

    return {
        "table": table,
        "tag": _effective.get(table),
        "effective": effective,
        "exemptions": exemptions,
        "dependency_order": order,
        "batches": batches,
        "skipped": skipped,
        "deleted": len(deleted),
    }
