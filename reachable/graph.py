"""依赖可达性判定，当前只按版本字符串匹配。"""

_results = []


def reachable(deps):
    if not isinstance(deps, dict):
        raise ValueError("deps 需要字典")
    modules = {}
    for module in deps.get("modules", []):
        modules[module.get("name")] = module
    hits = []
    for vuln in deps.get("vulnerable", []):
        module = modules.get(vuln.get("name"))
        if module is None:
            continue
        if module.get("version") != vuln.get("version"):
            continue
        hits.append(
            {
                "component": module.get("name"),
                "version": module.get("version"),
                "path": [],
                "entry": None,
            }
        )
    result = {
        "project": deps.get("project"),
        "reachable": hits,
        "paths": [],
        "unreachable": [],
        "pending": [],
        "basis": "exact-version",
    }
    _results.append(result)
    return dict(result)


def report(project=None):
    if project is None:
        rows = list(_results)
    else:
        rows = [row for row in _results if row["project"] == project]
    return {
        "project": project,
        "count": len(rows),
        "results": rows,
        "pending": [],
    }
