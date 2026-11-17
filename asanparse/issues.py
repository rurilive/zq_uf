"""内存检测报告解析入口：parse / locate / groups / report。

设计要点：
- 按报告结构提取错误类型、出错地址、调用栈和分配位置；
- 同一处问题按「类型 + 调用栈签名」归并为 group，多次运行/多份报告合并展示；
- 地址映射到源码时给出 file/line/function，映射不上时 mapping 显式标 unmapped；
- 报告格式变化导致解析失败时保留原文（kept），不静默返回空结果。
"""

import copy

from .parsing import is_error_marker, parse_block, split_blocks

# 注册表：id -> 条目。issue-* 是单次报告中的问题记录，group-* 是归并条目。
_ISSUES = {}


def _next_id(prefix):
    used = [
        int(k.rsplit("-", 1)[1])
        for k in _ISSUES
        if k.startswith(prefix) and k[len(prefix):].isdigit()
    ]
    return "%s%d" % (prefix, (max(used) + 1) if used else 1)


def clear():
    """清空所有已解析记录（测试和多次独立解析之间用）。"""
    _ISSUES.clear()


def _mapping(occ):
    """问题级映射状态：出错帧（#0）能映射即 mapped，否则 unmapped。"""
    if occ["frames"]:
        return occ["frames"][0]["mapping"]
    return "mapped" if occ.get("summary") and occ["summary"].get("source") else "unmapped"


def _signature(occ):
    """按调用栈归并：函数 + 文件:行优先；没有源码时用 序号+函数名
    （帧地址跨运行会变，不能当归并键）；连函数名都没有才退到帧地址。"""
    parts = []
    for frame in occ["frames"]:
        src = frame.get("source")
        if src:
            parts.append(
                "%s@%s:%s" % (frame.get("function") or "?", src["file"], src["line"])
            )
        elif frame.get("function"):
            parts.append("#%s:%s" % (frame["index"], frame["function"]))
        else:
            parts.append("#%s@%s" % (frame["index"], frame["address"]))
    return "|".join(parts) or "<no-stack>"


def _new_group(issue_id, occ):
    return {
        "id": None,  # 入注册表时填
        "kind": "group",
        "type": occ["type"],
        "signature": _signature(occ),
        "issue_ids": [issue_id],
        "count": 1,
        "reports": [],
        "addresses": [],
        "mapping": _mapping(occ),
        "mapped_source": occ["frames"][0]["source"] if occ["frames"] and occ["frames"][0]["source"] else None,
        "function": occ["frames"][0].get("function") if occ["frames"] else None,
        "frames": copy.deepcopy(occ["frames"]),
        "alloc": copy.deepcopy(occ.get("alloc")),
        "raw": occ["raw"],
    }


def _append_variant(group, issue, occ):
    """同一归并条目在不同报告里的地址差异单独列出。"""
    existing = {v["address"] for v in group["addresses"]}
    if occ["address"] and occ["address"] not in existing:
        top = occ["frames"][0] if occ["frames"] else None
        src = top["source"] if top else None
        group["addresses"].append({
            "address": occ["address"],
            "report": issue["report"],
            "issue_id": issue["id"],
            "mapping": top["mapping"] if top else "unmapped",
            "file": src["file"] if src else None,
            "line": src["line"] if src else None,
            "function": top.get("function") if top else None,
        })


def _register_occ(occ, report_name):
    issue_id = _next_id("issue-")
    issue = {
        "id": issue_id,
        "kind": "issue",
        "type": occ["type"],
        "address": occ["address"],
        "frames": occ["frames"],
        "freed_frames": occ["freed_frames"],
        "alloc": occ["alloc"],
        "alloc_frames": occ["alloc_frames"],
        "leak_kind": occ["leak_kind"],
        "summary": occ["summary"],
        "source": occ["frames"][0]["source"] if occ["frames"] and occ["frames"][0]["source"] else {},
        "mapping": _mapping(occ),
        "group": None,
        "report": report_name,
        "raw": occ["raw"],
    }
    sig = _signature(occ)
    target = None
    for entry in _ISSUES.values():
        if (
            entry.get("kind") == "group"
            and entry["type"] == occ["type"]
            and entry["signature"] == sig
        ):
            target = entry
            break
    if target is None:
        target = _new_group(issue_id, occ)
        target["id"] = _next_id("group-")
        _ISSUES[target["id"]] = target
    else:
        target["issue_ids"].append(issue_id)
        target["count"] += 1
        if target["mapping"] == "unmapped" and _mapping(occ) == "mapped":
            target["mapping"] = "mapped"
            top = occ["frames"][0]
            target["mapped_source"] = top["source"]
            target["function"] = top.get("function")
    issue["group"] = target["id"]
    target["reports"].append(report_name)
    _append_variant(target, issue, occ)
    _ISSUES[issue_id] = issue
    return issue, target


def parse(text, report_name=None):
    """解析一份内存检测报告文本。

    返回：
    - issues: 本次解析出的问题条目（每次运行的每次报告各一条）
    - groups: 归并后的条目（同一处问题的多次报告合并展示）
    - kept:  像错误报告但解析失败的片段，保留原文和原因，绝不静默吞掉
    - count: 本次解析出的问题条数
    """
    if not isinstance(text, str):
        raise ValueError("report must be a string")
    if report_name is None:
        report_name = "report-%d" % (
            1 + sum(
                1
                for v in _ISSUES.values()
                if v.get("kind") == "issue" and v["report"].startswith("report-")
            )
        )

    issues = []
    kept = []
    blocks = split_blocks(text)
    for lines in blocks:
        occs, reason = parse_block(lines)
        if reason is not None:
            kept_id = _next_id("kept-")
            entry = {
                "id": kept_id,
                "kind": "kept",
                "reason": reason,
                "report": report_name,
                "raw": "\n".join(lines),
            }
            kept.append(entry)
            _ISSUES[kept_id] = entry
            continue
        for occ in occs:
            issue, _ = _register_occ(occ, report_name)
            issues.append(issue)

    if not blocks:
        # 全文没有任何 ERROR 标记：整段留档，同样不静默返回空
        if text.strip():
            kept_id = _next_id("kept-")
            entry = {
                "id": kept_id,
                "kind": "kept",
                "reason": "no error marker found in report",
                "report": report_name,
                "raw": text,
            }
            kept.append(entry)
            _ISSUES[kept_id] = entry

    return {
        "issues": [copy.deepcopy(i) for i in issues],
        "groups": groups(),
        "kept": copy.deepcopy(kept),
        "count": len(issues),
    }


def locate(issue_id):
    """按 id 取条目（issue-*/group-*/kept-* 都可），取不到返回空字典。"""
    return copy.deepcopy(_ISSUES.get(issue_id) or {})


def groups(issue_type=None):
    """归并条目列表，可按问题类型筛选，便于批量修复。"""
    result = []
    for entry in _ISSUES.values():
        if entry.get("kind") != "group":
            continue
        if issue_type is not None and entry["type"] != issue_type:
            continue
        result.append(copy.deepcopy(entry))
    result.sort(key=lambda g: g["id"])
    return result


def kept():
    """解析失败而保留原文的片段（格式变化留档），按 id 排序。"""
    result = [
        copy.deepcopy(v)
        for v in _ISSUES.values()
        if v.get("kind") == "kept"
    ]
    result.sort(key=lambda k: k["id"])
    return result


def report(issue_type=None):
    """汇总：问题计数、类型分布、归并条目和映射状态，可按类型筛选。"""
    all_issues = [
        v
        for v in _ISSUES.values()
        if v.get("kind") == "issue"
        and (issue_type is None or v["type"] == issue_type)
    ]
    types = {}
    for issue in all_issues:
        types[issue["type"]] = types.get(issue["type"], 0) + 1
    grp = groups(issue_type)
    mapped = sum(1 for g in grp if g["mapping"] == "mapped")
    return {
        "issues": len(all_issues),
        "types": types,
        "groups": grp,
        "mapped": mapped,
        "unmapped": len(grp) - mapped,
        "kept": len(kept()),
    }
