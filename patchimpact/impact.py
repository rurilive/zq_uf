"""补丁影响评估：按依赖层级和版本范围分析受影响组件。

组件条目字段：
- name / version / environment / depends_on（依赖名列表，可带 "name>=1.0,<2.0" 内联约束）
- requires / constraints：{依赖名: 版本范围}，如 {"libxml2": ">=2.0,<2.9.1"}

补丁字段：name / version / components，可选 affects（影响版本范围，默认 "==version"）、
environment（目标环境）。
"""

import json

_assessments = []

DEEP_LEVEL = 3  # 依赖层级达到该值视为深层依赖，单独提示风险

_OPS = (">=", "<=", "==", "!=", ">", "<")


def _parse_version(text):
    parts = []
    for chunk in str(text).replace("-", ".").split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        parts.append(int(num))
    return tuple(parts) if parts else (0,)


def _cmp(a, b):
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def _parse_spec(spec):
    clauses = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        for op in _OPS:
            if part.startswith(op):
                clauses.append((op, _parse_version(part[len(op):].strip())))
                break
        else:
            clauses.append(("==", _parse_version(part)))
    return clauses


def _satisfies(version, clauses):
    for op, target in clauses:
        c = _cmp(version, target)
        if op == ">=" and c < 0:
            return False
        if op == "<=" and c > 0:
            return False
        if op == ">" and c <= 0:
            return False
        if op == "<" and c >= 0:
            return False
        if op == "==" and c != 0:
            return False
        if op == "!=" and c == 0:
            return False
    return True


def _bounds(clauses):
    lower = None  # (version, inclusive)
    upper = None
    for op, v in clauses:
        if op == "==":
            lower = upper = (v, True)
        elif op in (">=", ">"):
            cand = (v, op == ">=")
            if lower is None or _cmp(cand[0], lower[0]) > 0:
                lower = cand
        elif op in ("<=", "<"):
            cand = (v, op == "<=")
            if upper is None or _cmp(cand[0], upper[0]) < 0:
                upper = cand
        # "!=" 不改变区间
    return lower, upper


def _overlaps(spec_a, spec_b):
    la, ua = _bounds(_parse_spec(spec_a))
    lb, ub = _bounds(_parse_spec(spec_b))
    if ua and lb:
        c = _cmp(ua[0], lb[0])
        if c < 0 or (c == 0 and not (ua[1] and lb[1])):
            return False
    if ub and la:
        c = _cmp(ub[0], la[0])
        if c < 0 or (c == 0 and not (ub[1] and la[1])):
            return False
    return True


def _split_dep(dep):
    text = str(dep)
    for op in _OPS:
        if op in text:
            name, _, ver = text.partition(op)
            return name.strip(), (op + ver.strip())
    return text.strip(), None


def _dep_names(entry):
    return [_split_dep(d)[0] for d in entry.get("depends_on", [])]


def _constraints(entry):
    reqs = dict(entry.get("requires") or entry.get("constraints") or {})
    for dep in entry.get("depends_on", []):
        name, spec = _split_dep(dep)
        if spec:
            reqs.setdefault(name, spec)
    return reqs


def _risk(level):
    if level >= DEEP_LEVEL:
        return "high"
    if level == DEEP_LEVEL - 1:
        return "medium"
    return "low"


def _edge_reason(entry, dep, patch_name, affects, versions):
    """版本不在范围内时返回排除理由，否则返回 None。"""
    spec = _constraints(entry).get(dep)
    if not spec:
        return None
    if dep == patch_name:
        if affects and not _overlaps(spec, affects):
            return "依赖 %s 的版本范围 %s 与补丁影响范围 %s 不重叠" % (dep, spec, affects)
        return None
    dep_version = versions.get(dep)
    if dep_version is None:
        return None
    if not _satisfies(_parse_version(dep_version), _parse_spec(spec)):
        return "依赖 %s 的版本 %s 不在范围 %s 内" % (dep, dep_version, spec)
    return None


def assess(patch):
    if not isinstance(patch, dict):
        raise ValueError("patch 需要字典")
    name = patch.get("name")
    if not name:
        raise ValueError("缺少补丁名称")
    patch_version = patch.get("version")
    affects = patch.get("affects") or ("==%s" % patch_version if patch_version else None)
    entries = [e for e in patch.get("components", []) if isinstance(e, dict) and e.get("name")]

    versions = {}
    for e in entries:
        versions.setdefault(e["name"], e.get("version"))

    # 从补丁组件出发按依赖边逐层扩散（BFS），版本不在范围内的边不参与传播
    levels = {}
    paths = {}
    affected = set()
    invalid = {}
    queue = [(name, 0, [name])]
    while queue:
        cur, level, path = queue.pop(0)
        for e in entries:
            if cur not in _dep_names(e):
                continue
            reason = _edge_reason(e, cur, name, affects, versions)
            if reason:
                invalid.setdefault(id(e), reason)
                continue
            if e["name"] not in levels:
                levels[e["name"]] = level + 1
                paths[e["name"]] = path + [e["name"]]
                queue.append((e["name"], level + 1, paths[e["name"]]))
            affected.add(id(e))

    components = []
    excluded = []
    for e in entries:
        if id(e) in affected:
            level = levels[e["name"]]
            components.append(
                {
                    "name": e["name"],
                    "component_version": e.get("version"),
                    "environment": e.get("environment"),
                    "level": level,
                    "indirect": level > 1,
                    "risk": _risk(level),
                    "path": list(paths[e["name"]]),
                }
            )
        elif id(e) in invalid:
            excluded.append(
                {
                    "name": e["name"],
                    "component_version": e.get("version"),
                    "environment": e.get("environment"),
                    "reason": invalid[id(e)],
                }
            )
    components.sort(key=lambda c: c["name"])
    excluded.sort(key=lambda c: c["name"])

    environments = sorted({c["environment"] for c in components if c["environment"]})
    result = {
        "patch": name,
        "patch_version": patch_version,
        "target_environment": patch.get("environment") or patch.get("target_environment"),
        "components": components,
        "environments": environments,
        "max_level": max(levels.values(), default=0),
        "deep_components": sorted({c["name"] for c in components if c["level"] >= DEEP_LEVEL}),
        "excluded": excluded,
    }
    _assessments.append(result)
    return result


def report(patch=None):
    if patch is None:
        rows = list(_assessments)
    else:
        rows = [row for row in _assessments if row["patch"] == patch]
    excluded = []
    for row in rows:
        excluded.extend(row["excluded"])
    environments = sorted({env for row in rows for env in row["environments"]})
    return {
        "patch": patch,
        "count": len(rows),
        "assessments": rows,
        "environments": environments,
        "excluded": excluded,
    }


def _env_view(result, environment):
    components = [c for c in result["components"] if c["environment"] == environment]
    excluded = [e for e in result["excluded"] if e["environment"] == environment]
    return {
        "patch": result["patch"],
        "patch_version": result["patch_version"],
        "environment": environment,
        "components": components,
        "excluded": excluded,
        "max_level": max((c["level"] for c in components), default=0),
        "deep_components": sorted({c["name"] for c in components if c["level"] >= DEEP_LEVEL}),
    }


def export(result, environment=None, path=None):
    """按环境导出评估结果。指定环境返回该环境的子报告，
    否则返回 {环境: 子报告}；给 path 时同时写 JSON 文件。"""
    data = (
        _env_view(result, environment)
        if environment is not None
        else {env: _env_view(result, env) for env in result["environments"]}
    )
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    return data


def diff(first, second):
    """对比同一补丁的两次评估，返回组件、层级和排除项的差异。"""

    def key(c):
        return (c["name"], c.get("environment"))

    def index(rows):
        return {key(c): c for c in rows}

    a, b = index(first["components"]), index(second["components"])
    xa, xb = index(first["excluded"]), index(second["excluded"])
    return {
        "patch": second.get("patch") or first.get("patch"),
        "from_version": first.get("patch_version"),
        "to_version": second.get("patch_version"),
        "added_components": [b[k] for k in sorted(b) if k not in a],
        "removed_components": [a[k] for k in sorted(a) if k not in b],
        "level_changes": [
            {"name": k[0], "environment": k[1], "from": a[k]["level"], "to": b[k]["level"]}
            for k in sorted(a)
            if k in b and a[k]["level"] != b[k]["level"]
        ],
        "added_excluded": [xb[k] for k in sorted(xb) if k not in xa],
        "removed_excluded": [xa[k] for k in sorted(xa) if k not in xb],
    }


def reverse_lookup(component):
    """从组件名反查：哪些补丁评估影响到了它。"""
    hits = []
    for row in _assessments:
        for c in row["components"]:
            if c["name"] == component:
                hits.append(
                    {
                        "patch": row["patch"],
                        "patch_version": row["patch_version"],
                        "environment": c["environment"],
                        "level": c["level"],
                        "risk": c["risk"],
                    }
                )
    return hits
