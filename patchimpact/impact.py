"""补丁影响评估，当前只看一层的直接依赖。"""

_assessments = []


def assess(patch):
    if not isinstance(patch, dict):
        raise ValueError("patch 需要字典")
    name = patch.get("name")
    if not name:
        raise ValueError("缺少补丁名称")
    components = []
    for item in patch.get("components", []):
        if name in item.get("depends_on", []):
            components.append(
                {
                    "name": item.get("name"),
                    "component_version": item.get("version"),
                    "environment": item.get("environment"),
                    "level": 1,
                }
            )
    environments = sorted({c["environment"] for c in components if c["environment"]})
    result = {
        "patch": name,
        "patch_version": patch.get("version"),
        "components": components,
        "environments": environments,
        "max_level": 1,
        "excluded": [],
    }
    _assessments.append(result)
    return result


def report(patch=None):
    if patch is None:
        rows = list(_assessments)
    else:
        rows = [row for row in _assessments if row["patch"] == patch]
    return {
        "patch": patch,
        "count": len(rows),
        "assessments": rows,
        "excluded": [],
    }
