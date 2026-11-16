"""内存检测报告解析，当前按固定行格式切段。"""

_ISSUES = {}


def _frames(lines):
    frames = []
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        frames.append({
            "index": parts[0].lstrip("#"),
            "address": parts[1],
            "raw": line,
        })
    return frames


def parse(text):
    """只认 ERROR 开头的行，格式不对就当没有。"""
    if not isinstance(text, str):
        raise ValueError("report must be a string")
    blocks = []
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("ERROR:"):
            parts = line.split()
            if len(parts) < 3:
                continue
            current = {
                "type": parts[1],
                "address": parts[2],
                "frames": [],
                "alloc": "",
            }
            blocks.append(current)
        elif current is not None and line.startswith("#"):
            current["frames"].append(line)
        elif current is not None and line.startswith("ALLOC"):
            current["alloc"] = line

    issues = []
    for index, block in enumerate(blocks):
        issue = {
            "id": "issue-%d" % (index + 1),
            "type": block["type"],
            "address": block["address"],
            "frames": _frames(block["frames"]),
            "alloc": block["alloc"],
            "source": {},
            "group": "",
        }
        issues.append(issue)
    for issue in issues:
        _ISSUES[issue["id"]] = issue
    return {
        "issues": issues,
        "count": len(issues),
        "kept": 0,
    }


def locate(issue_id):
    """按条目编号取一条，取不到返回空字典。"""
    return dict(_ISSUES.get(issue_id) or {})


def report():
    """按类型简单计数，不归并也不给源码位置。"""
    types = {}
    for issue in _ISSUES.values():
        types[issue["type"]] = types.get(issue["type"], 0) + 1
    return {
        "issues": len(_ISSUES),
        "types": types,
        "groups": [],
        "mapped": 0,
    }
