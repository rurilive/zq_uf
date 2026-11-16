"""依赖可达性判定。

从外部入口出发沿调用边遍历，判定带漏洞组件是否真正被调用：
- 可达：给出全部调用路径，每条注明入口及入口来源；
- 不可达：写明原因；
- 运行期加载 / 反射调用：不可静态判定，单独列为待确认；
- 版本匹配先做归一化（"1.2" 与 "1.2.0"、"v2.14.1" 与 "2.14.1" 视为一致），
  结论里保留组件实际版本与匹配依据。

deps 输入格式（在原有 modules / vulnerable 之上扩展，均可选）::

    {
        "project": "shop",
        "modules":    [{"name": "log4j", "version": "2.14.1"}, ...],
        "vulnerable": [{"name": "log4j", "version": "2.14.1"}, ...],
        "entries":    ["shop.web.main",                       # 字符串
                       {"name": "shop.api.pay", "source": "http"}],  # 或带来源
        "calls":      [{"from": "shop.web.main", "to": "log4j"}, ...],
        "dynamic":    [{"component": "groovy", "via": "reflection"},
                       "fastjson"],                            # 字符串亦可
    }

calls 的 to 既可以是函数名，也可以直接是组件名（表示调用了该组件）。
"""

import re
from collections import deque

_results = []

_VERSION_SPLIT = re.compile(r"[.\-_+~]")


def _norm_version(version):
    """把版本号拆成段序列，数字段转 int，便于跨写法比较。"""
    if version is None:
        return ()
    text = str(version).strip().lower()
    if text.startswith("v") and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    parts = []
    for seg in _VERSION_SPLIT.split(text):
        if not seg:
            continue
        if seg.isdigit():
            parts.append((0, int(seg)))
        else:
            parts.append((1, seg))
    return tuple(parts)


def _strip_trailing_zeros(parts):
    parts = list(parts)
    while parts and parts[-1] == (0, 0):
        parts.pop()
    return tuple(parts)


def _version_match(module_version, vuln_version):
    """返回匹配依据（exact / normalized），不匹配返回 None。"""
    if module_version == vuln_version:
        return "exact"
    if module_version is None or vuln_version is None:
        return None
    a = _strip_trailing_zeros(_norm_version(module_version))
    b = _strip_trailing_zeros(_norm_version(vuln_version))
    if a and a == b:
        return "normalized"
    return None


def _norm_entry(entry):
    """入口统一成 (name, source)。"""
    if isinstance(entry, dict):
        return entry.get("name"), entry.get("source")
    return entry, None


def _norm_dynamic(item):
    """动态加载项统一成 (component, via)。"""
    if isinstance(item, dict):
        return item.get("component") or item.get("name"), item.get("via", "runtime-load")
    return item, "runtime-load"


def _build_adjacency(calls):
    adj = {}
    for edge in calls:
        src = edge.get("from")
        dst = edge.get("to")
        if not src or not dst:
            continue
        adj.setdefault(src, []).append(dst)
    return adj


def _find_paths(entries, adj, targets, max_paths_per_entry=8):
    """从每个入口 BFS，返回 {target: [{"entry", "source", "path"}]}。

    同一组件在多个入口可达时合并到同一条记录，路径全部保留。
    """
    found = {t: [] for t in targets}
    for raw_entry in entries:
        entry, source = _norm_entry(raw_entry)
        if not entry:
            continue
        queue = deque([[entry]])
        visited = {entry}
        per_entry = 0
        while queue:
            path = queue.popleft()
            node = path[-1]
            if node in targets and len(path) > 1:
                found[node].append(
                    {"entry": entry, "source": source, "path": list(path)}
                )
                per_entry += 1
                if per_entry >= max_paths_per_entry:
                    break
            for nxt in adj.get(node, []):
                if nxt in visited:
                    continue
                visited.add(nxt)
                queue.append(path + [nxt])
    return found


def _unreachable_reason(component, adj):
    called_somewhere = any(component in dsts for dsts in adj.values())
    if called_somewhere:
        return "组件被调用但调用链与外部入口不连通"
    return "未被任何代码调用"


def reachable(deps):
    if not isinstance(deps, dict):
        raise ValueError("deps 需要字典")

    modules = {}
    for module in deps.get("modules", []):
        modules[module.get("name")] = module

    # 1) 版本归一化匹配，先确定本项目实际受影响的组件
    affected = []  # (component, module, vuln, match_basis)
    for vuln in deps.get("vulnerable", []):
        module = modules.get(vuln.get("name"))
        if module is None:
            continue
        basis = _version_match(module.get("version"), vuln.get("version"))
        if basis is None:
            continue
        affected.append((module.get("name"), module, vuln, basis))

    targets = {name for name, _, _, _ in affected}

    # 2) 从入口沿调用边找路径
    adj = _build_adjacency(deps.get("calls", []))
    paths_by_target = _find_paths(deps.get("entries", []), adj, targets)

    # 3) 运行期加载 / 反射 → 待确认
    dynamic = {}
    for item in deps.get("dynamic", []):
        component, via = _norm_dynamic(item)
        if component:
            dynamic[component] = via
    for module in deps.get("modules", []):
        if module.get("dynamic"):
            dynamic.setdefault(module.get("name"), "runtime-load")

    reachable_hits = []
    unreachable = []
    pending = []
    for name, module, vuln, basis in affected:
        match_info = {
            "basis": basis,
            "module_version": module.get("version"),
            "vuln_version": vuln.get("version"),
        }
        paths = paths_by_target.get(name, [])
        if paths:
            reachable_hits.append(
                {
                    "component": name,
                    "version": module.get("version"),
                    "paths": paths,
                    "entries": sorted({p["entry"] for p in paths}),
                    "match": match_info,
                }
            )
        elif name in dynamic:
            pending.append(
                {
                    "component": name,
                    "version": module.get("version"),
                    "via": dynamic[name],
                    "reason": "运行期加载/反射调用，静态调用图无法确认",
                    "match": match_info,
                }
            )
        else:
            unreachable.append(
                {
                    "component": name,
                    "version": module.get("version"),
                    "reason": _unreachable_reason(name, adj),
                    "match": match_info,
                }
            )

    result = {
        "project": deps.get("project"),
        "reachable": reachable_hits,
        "unreachable": unreachable,
        "pending": pending,
        "basis": "call-graph+normalized-version",
    }
    _results.append(result)
    return dict(result)


def report(project=None):
    """按项目汇总：可达组件、调用路径、不可达原因、待确认项。"""
    if project is None:
        rows = list(_results)
    else:
        rows = [row for row in _results if row["project"] == project]
    return {
        "project": project,
        "count": len(rows),
        "reachable": [hit for row in rows for hit in row["reachable"]],
        "unreachable": [item for row in rows for item in row["unreachable"]],
        "pending": [item for row in rows for item in row["pending"]],
        "results": rows,
    }
