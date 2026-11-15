"""依赖驱动的任务执行。

- 按拓扑序执行；上游失败时下游标记 skipped 并记录原因，不再执行。
- ``run`` / ``rerun`` 复用已成功节点的结果，只重算失败和跳过的节点。
- ``hot_update`` 先校验依赖变更（缺失依赖、成环），校验失败保留旧图继续调度。
"""

from __future__ import annotations

OK = "ok"
FAILED = "failed"
SKIPPED = "skipped"


class DagError(Exception):
    """依赖图不合法：缺失依赖、成环或引用未知节点。"""

    def __init__(self, message, *, missing=None, cycle=None):
        super().__init__(message)
        self.missing = list(missing or [])
        self.cycle = list(cycle or [])


_state = {
    "specs": {},          # name -> {"deps": [...], "fail": bool}
    "status": {},         # name -> ok | failed | skipped
    "results": {},        # name -> 最近一次成功结果
    "skip_reasons": {},
    "errors": {},
    "version": 0,
    "last_update": None,
}


# --------------------------------------------------------------------------
# 图解析与校验
# --------------------------------------------------------------------------

def _dedupe(items):
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _as_deps(value):
    """依赖声明可以是单个名字或名字列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple, set)):
        raise DagError(f"deps must be a name or list of names, got {value!r}")
    return list(value)


def _node_spec(item):
    """解析字符串或 dict 形式的节点声明，返回 (名字, 依赖, 失败标记)。

    失败标记返回 ``None`` 表示调用方未声明，由调用方决定默认值。
    """
    if isinstance(item, str):
        if not item:
            raise DagError("node name must not be empty")
        return item, [], None
    if isinstance(item, dict):
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise DagError(f"invalid node spec: {item!r}")
        fail = bool(item["fail"]) if "fail" in item else None
        return name, _as_deps(item.get("deps")), fail
    raise DagError(f"invalid node spec: {item!r}")


def _find_cycle(specs):
    """DFS 找一个环，返回形如 [a, b, a] 的路径；无环返回 None。

    迭代实现，避免长链路触发递归上限。
    """
    white, gray, black = 0, 1, 2
    color = {name: white for name in specs}
    for root in specs:
        if color[root] != white:
            continue
        path = [root]
        frames = [iter(specs[root]["deps"])]
        color[root] = gray
        while frames:
            dep = next(frames[-1], None)
            if dep is None:
                color[path.pop()] = black
                frames.pop()
                continue
            if color[dep] == gray:
                return path[path.index(dep):] + [dep]
            if color[dep] == white:
                color[dep] = gray
                path.append(dep)
                frames.append(iter(specs[dep]["deps"]))
    return None


def _validate(specs):
    missing = []
    for name, spec in specs.items():
        for dep in spec["deps"]:
            if not isinstance(dep, str) or not dep:
                raise DagError(f"dependency of {name!r} must be a non-empty string")
            if dep not in specs:
                missing.append((dep, name))
    if missing:
        message = "; ".join(
            f"missing dependency {dep!r} required by {node!r}"
            for dep, node in missing
        )
        raise DagError(message, missing=[dep for dep, _ in missing])
    cycle = _find_cycle(specs)
    if cycle:
        raise DagError(
            "dependency cycle detected: " + " -> ".join(cycle), cycle=cycle
        )


def _normalize(dag):
    if not isinstance(dag, dict):
        raise DagError("dag must be a dict with a 'nodes' list")
    specs = {}
    for item in dag.get("nodes") or []:
        name, deps, fail = _node_spec(item)
        if name in specs:
            raise DagError(f"duplicate node: {name!r}")
        specs[name] = {"deps": _dedupe(deps), "fail": bool(fail)}
    for name, deps in (dag.get("deps") or {}).items():
        if name not in specs:
            raise DagError(f"deps refers to unknown node: {name!r}")
        specs[name]["deps"] = _dedupe(_as_deps(deps))
    _validate(specs)
    return specs


def _topo(specs):
    """稳定拓扑序：同层节点按声明顺序出队。"""
    indegree = {name: len(spec["deps"]) for name, spec in specs.items()}
    children = {name: [] for name in specs}
    for name, spec in specs.items():
        for dep in spec["deps"]:
            children[dep].append(name)
    queue = [name for name in specs if indegree[name] == 0]
    order = []
    index = 0
    while index < len(queue):
        node = queue[index]
        index += 1
        order.append(node)
        for child in children[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(order) != len(specs):
        cycle = _find_cycle(specs) or []
        raise DagError(
            "dependency cycle detected: " + " -> ".join(cycle), cycle=cycle
        )
    return order


def _graph_view(specs):
    return {name: list(spec["deps"]) for name, spec in specs.items()}


def _descendants(specs, roots):
    """返回 roots 及全部下游节点（沿依赖边向下）。"""
    children = {name: [] for name in specs}
    for name, spec in specs.items():
        for dep in spec["deps"]:
            children[dep].append(name)
    seen = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(children.get(node, []))
    return seen


def _invalidate(names):
    """丢弃指定节点的缓存结果，下一次调度重算它们。"""
    for name in names:
        _state["status"].pop(name, None)
        _state["results"].pop(name, None)
        _state["skip_reasons"].pop(name, None)
        _state["errors"].pop(name, None)


# --------------------------------------------------------------------------
# 提交新图
# --------------------------------------------------------------------------

def _changed_roots(old, new):
    """返回依赖或失败标记发生变化的节点（新节点也算）。"""
    roots = []
    for name, spec in new.items():
        previous = old.get(name)
        if (
            previous is None
            or previous["deps"] != spec["deps"]
            or previous["fail"] != spec["fail"]
        ):
            roots.append(name)
    return roots


def _commit(specs):
    """校验并生效新图，返回 (新增节点, 变化节点, 是否更新了图)。

    新增/变化节点及其下游的旧结果作废；其余成功节点的结果继续复用。
    图没有实质变化时不作废结果、不提升版本号。
    """
    old = _state["specs"]
    added = sorted(name for name in specs if name not in old)
    changed = sorted(
        name for name in _changed_roots(old, specs) if name in old
    )
    removed = sorted(set(old) - set(specs))
    _state["specs"] = specs
    if not (added or changed or removed):
        return added, changed, False
    stale = _descendants(specs, added + changed) | set(removed)
    _invalidate(stale)
    _state["version"] += 1
    return added, changed, True


# --------------------------------------------------------------------------
# 调度
# --------------------------------------------------------------------------

def _skip_reason(bad_deps, status):
    parts = [f"{dep} {status[dep]}" for dep in bad_deps]
    return "upstream not ok: " + ", ".join(parts)


def _invoke(name, spec, execute):
    # ``fail`` 用于声明式注入失败，便于验证失败传播。
    if spec["fail"]:
        raise RuntimeError(f"node {name!r} failed")
    if execute is not None:
        return execute(name)
    return {"node": name}


def _schedule(execute):
    specs = _state["specs"]
    order = _topo(specs)
    status, results = {}, {}
    skip_reasons, errors = {}, {}
    executed, reused, skipped, failed = [], [], [], []

    for name in order:
        spec = specs[name]
        # 上游没成功，一律跳过并写原因，即使本节点上一轮成功过。
        bad = [dep for dep in spec["deps"] if status[dep] != OK]
        if bad:
            status[name] = SKIPPED
            skip_reasons[name] = _skip_reason(bad, status)
            skipped.append(name)
            continue

        # 成功节点复用已有结果，只重算失败和跳过的节点。
        if _state["status"].get(name) == OK and name in _state["results"]:
            status[name] = OK
            results[name] = _state["results"][name]
            reused.append(name)
            continue

        try:
            results[name] = _invoke(name, spec, execute)
        except Exception as exc:  # 节点失败，向下游传播
            status[name] = FAILED
            errors[name] = str(exc) or exc.__class__.__name__
            failed.append(name)
            executed.append(name)
        else:
            status[name] = OK
            executed.append(name)

    _state.update(
        status=status,
        results=results,
        skip_reasons=skip_reasons,
        errors=errors,
    )
    return {
        "status": status,
        "order": order,
        "skipped": skipped,
        "skip_reasons": skip_reasons,
        "failed": failed,
        "errors": errors,
        "rerun": executed,
        "reused": reused,
        "ok": not skipped and not failed,
        "update": _state["last_update"],
        "graph_version": _state["version"],
    }


def run(dag=None, execute=None):
    """调度整张图。

    ``dag`` 为空时重跑当前图；已成功节点复用结果，失败/跳过节点重算。
    新图先校验，校验失败抛 ``DagError``，旧图保持不变。
    """
    if dag is not None:
        _commit(_normalize(dag))
    elif not _state["specs"]:
        raise DagError("no pipeline: call run(dag) first")
    return _schedule(execute)


def rerun(nodes=None, execute=None):
    """重跑失败/跳过的节点。

    ``nodes`` 为空时重跑所有失败和跳过的节点；给定时只重跑这些节点，
    其余成功节点复用已有结果，被重算节点的下游也会跟着重算。
    """
    if not _state["specs"]:
        raise DagError("no pipeline: call run(dag) first")
    if nodes is None:
        targets = {
            name
            for name, value in _state["status"].items()
            if value in (FAILED, SKIPPED)
        }
    else:
        if isinstance(nodes, str):
            nodes = [nodes]
        targets = set(nodes)
        unknown = sorted(targets - set(_state["specs"]))
        if unknown:
            raise DagError("unknown node(s): " + ", ".join(unknown))
    # 点名重跑的节点强制重算，其下游结果一并作废。
    _invalidate(targets | _descendants(_state["specs"], targets))
    return _schedule(execute)


# --------------------------------------------------------------------------
# 依赖热更新
# --------------------------------------------------------------------------

def _merge_changes(base, changes):
    if not isinstance(changes, dict):
        raise DagError("dependency changes must be a dict")
    specs = {
        name: {"deps": list(spec["deps"]), "fail": spec["fail"]}
        for name, spec in base.items()
    }
    if "nodes" in changes or "deps" in changes:
        raw_nodes = changes.get("nodes") or []
        dep_patch = changes.get("deps") or {}
    else:
        raw_nodes, dep_patch = [], changes

    for item in raw_nodes:
        name, deps, fail = _node_spec(item)
        if name in specs:
            # 未给出的字段沿用旧值，便于只改依赖或只改失败标记。
            if deps:
                specs[name]["deps"] = _dedupe(deps)
            if fail is not None:
                specs[name]["fail"] = fail
        else:
            specs[name] = {"deps": _dedupe(deps), "fail": bool(fail)}

    for name, deps in dep_patch.items():
        if name not in specs:
            raise DagError(f"dependency change refers to unknown node: {name!r}")
        specs[name]["deps"] = _dedupe(_as_deps(deps))

    return specs


def _public_update(result):
    return {k: v for k, v in result.items() if k not in ("graph", "proposed")}


def hot_update(changes, *, apply=True):
    """校验依赖变更并热更新。

    ``changes`` 形如 ``{"nodes": [...], "deps": {name: [deps]}}``，``deps``
    按节点整体替换。``apply=False`` 只校验不生效（可放在非高峰时段预演）；
    ``apply=True`` 校验通过后生效，影响下一次调度。校验失败时返回明确错误，
    保留旧图，旧图仍可继续调度。
    """
    base = _state["specs"]
    try:
        merged = _merge_changes(base, changes)
        _validate(merged)
        order = _topo(merged)
    except DagError as exc:
        result = {
            "ok": False,
            "valid": False,
            "applied": False,
            "error": str(exc),
            "missing": exc.missing,
            "cycle": exc.cycle,
            "added": [],
            "changed": [],
            "updated": False,
            "version": _state["version"],
            "graph": _graph_view(base),
            "order": None,
        }
        _state["last_update"] = _public_update(result)
        return result

    result = {
        "ok": True,
        "valid": True,
        "applied": bool(apply),
        "error": None,
        "missing": [],
        "cycle": [],
        "added": [],
        "changed": [],
        "updated": bool(apply),
        "version": _state["version"],
        "graph": None,
        "order": order,
        "proposed": None,
    }
    if apply:
        result["added"], result["changed"], result["updated"] = _commit(merged)
        result["version"] = _state["version"]
        result["graph"] = _graph_view(_state["specs"])
    else:
        result["proposed"] = _graph_view(merged)
        result["graph"] = _graph_view(base)
    _state["last_update"] = _public_update(result)
    return result


def reset():
    """清空调度状态（图、缓存、版本号和最近一次热更新结果）。"""
    _state.update(
        specs={},
        status={},
        results={},
        skip_reasons={},
        errors={},
        version=0,
        last_update=None,
    )
