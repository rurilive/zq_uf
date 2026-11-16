"""崩溃归并，当前只按调用栈顶帧分组。

crash 是内存里的崩溃记录，带 id、target、stack、signal 和 fault_addr。
"""

_issues = []


def _top_frame(crash):
    """取栈顶函数名，偏移量不同也算同一帧。"""
    stack = crash.get("stack") or []
    if not stack:
        return "empty"
    return str(stack[0]).split("+", 1)[0].strip()


def triage(crashes):
    """同一栈顶就算同一问题，寄存器与出错指令不参与比较。"""
    global _issues
    if not isinstance(crashes, list):
        raise ValueError("crashes must be a list")
    groups = {}
    order = []
    for crash in crashes:
        if not isinstance(crash, dict) or "id" not in crash:
            raise ValueError("crash needs id")
        key = _top_frame(crash)
        if key not in groups:
            groups[key] = {
                "key": key,
                "target": crash.get("target"),
                "samples": [],
                "signals": set(),
            }
            order.append(key)
        groups[key]["samples"].append(crash["id"])
        groups[key]["signals"].add(crash.get("signal", "unknown"))

    _issues = []
    for key in order:
        item = groups[key]
        _issues.append(
            {
                "key": key,
                "target": item["target"],
                "count": len(item["samples"]),
                "samples": item["samples"],
                "signals": sorted(item["signals"]),
                "reason": "stack_top",
            }
        )
    return {"issues": _issues, "total": len(crashes)}


def report():
    """按目标统计样本数。"""
    targets = {}
    for issue in _issues:
        target = issue["target"] or "unknown"
        targets[target] = targets.get(target, 0) + issue["count"]
    return {
        "issues": len(_issues),
        "by_target": targets,
        "representative": 0,
    }
