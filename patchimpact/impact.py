"""补丁影响评估：按依赖层级和版本范围评估补丁影响面。

从补丁涉及的组件出发，沿 depends_on 找出直接和间接依赖它的组件，
标出受影响环境；版本不在补丁版本范围内的组件单独排除并说明理由。
"""

_assessments = []

# 依赖层级达到该值视为深层依赖，单独提示风险
_DEEP_LEVEL = 3

_OPERATORS = (">=", "<=", "==", "!=", ">", "<")


def _parse_version(text):
    parts = []
    for piece in str(text).split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _compare(version_a, version_b):
    a, b = _parse_version(version_a), _parse_version(version_b)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return (a > b) - (a < b)


def _in_range(version, spec):
    """判断版本是否满足范围表达式，如 ">=2.0,<3.0"；空范围视为始终满足。"""
    if version is None or not spec:
        return not spec
    for clause in str(spec).split(","):
        clause = clause.strip()
        if not clause:
            continue
        for op in _OPERATORS:
            if clause.startswith(op):
                cmp_result = _compare(version, clause[len(op):].strip())
                satisfied = {
                    ">=": cmp_result >= 0,
                    "<=": cmp_result <= 0,
                    "==": cmp_result == 0,
                    "!=": cmp_result != 0,
                    ">": cmp_result > 0,
                    "<": cmp_result < 0,
                }[op]
                if not satisfied:
                    return False
                break
        else:
            if _compare(version, clause) != 0:
                return False
    return True


def assess(patch):
    if not isinstance(patch, dict):
        raise ValueError("patch 需要字典")
    name = patch.get("name")
    if not name:
        raise ValueError("缺少补丁名称")

    dependents = {}
    for item in patch.get("components", []):
        for dependency in item.get("depends_on", []):
            dependents.setdefault(dependency, []).append(item)

    default_range = patch.get("version_range")
    version_ranges = patch.get("version_ranges", {})

    components = []
    excluded = []
    seen = set()
    frontier = [name]
    level = 0
    while frontier:
        level += 1
        next_level = []
        for dependency in frontier:
            for item in dependents.get(dependency, []):
                item_name = item.get("name")
                if not item_name or item_name in seen:
                    continue
                seen.add(item_name)
                spec = version_ranges.get(item_name, default_range)
                version = item.get("version")
                if spec and not _in_range(version, spec):
                    excluded.append(
                        {
                            "name": item_name,
                            "component_version": version,
                            "environment": item.get("environment"),
                            "level": level,
                            "reason": "版本 {} 不在补丁适用范围 {} 内".format(version, spec),
                        }
                    )
                    continue  # 被排除的版本不受补丁影响，不再向下传播
                deep = level >= _DEEP_LEVEL
                components.append(
                    {
                        "name": item_name,
                        "component_version": version,
                        "environment": item.get("environment"),
                        "level": level,
                        "direct": level == 1,
                        "deep": deep,
                        "risk": "依赖层级较深，影响沿链路放大，建议重点回归" if deep else None,
                    }
                )
                next_level.append(item_name)
        frontier = next_level

    components.sort(key=lambda c: c["name"])
    excluded.sort(key=lambda c: c["name"])
    environments = sorted({c["environment"] for c in components if c["environment"]})
    result = {
        "patch": name,
        "patch_version": patch.get("version"),
        "target_environment": patch.get("environment"),
        "components": components,
        "environments": environments,
        "max_level": max((c["level"] for c in components), default=0),
        "deep_components": [c["name"] for c in components if c["deep"]],
        "excluded": excluded,
    }
    _assessments.append(result)
    return result


def _filter_environment(row, environment):
    filtered = dict(row)
    filtered["components"] = [
        c for c in row["components"] if c["environment"] == environment
    ]
    filtered["excluded"] = [
        c for c in row["excluded"] if c["environment"] == environment
    ]
    filtered["environments"] = [environment] if filtered["components"] else []
    filtered["deep_components"] = [c["name"] for c in filtered["components"] if c["deep"]]
    filtered["max_level"] = max((c["level"] for c in filtered["components"]), default=0)
    return filtered


def report(patch=None, environment=None):
    """按补丁查看评估结果；指定 environment 时按环境分别导出。"""
    rows = [row for row in _assessments if patch is None or row["patch"] == patch]
    if environment is not None:
        rows = [_filter_environment(row, environment) for row in rows]
    return {
        "patch": patch,
        "environment": environment,
        "count": len(rows),
        "assessments": rows,
        "excluded": [item for row in rows for item in row["excluded"]],
    }


def diff(first, second):
    """对比同一补丁的两次评估结果，返回组件差异。"""
    before = {c["name"]: c for c in first.get("components", [])}
    after = {c["name"]: c for c in second.get("components", [])}
    before_excluded = {c["name"] for c in first.get("excluded", [])}
    after_excluded = {c["name"] for c in second.get("excluded", [])}
    return {
        "patch": second.get("patch", first.get("patch")),
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(n for n in set(before) & set(after) if before[n] != after[n]),
        "excluded_added": sorted(after_excluded - before_excluded),
        "excluded_removed": sorted(before_excluded - after_excluded),
    }


def find_by_component(component):
    """从组件名反查：哪些补丁的评估结果包含该组件。"""
    hits = []
    for row in _assessments:
        for item in row["components"]:
            if item["name"] == component:
                hits.append(
                    {
                        "patch": row["patch"],
                        "patch_version": row["patch_version"],
                        "target_environment": row["target_environment"],
                        "level": item["level"],
                        "environment": item["environment"],
                        "deep": item["deep"],
                    }
                )
    return sorted(hits, key=lambda h: (h["patch"], h["level"]))
