"""漏洞工单状态流转：按状态和证据流转，修复提交后必须复测通过才能关闭。"""

import time

_tickets = {}

# 各状态进入条件：是否需要责任人 / 修复证据 / 复测通过
_WORKFLOW = {
    "open": {"owner": False, "evidence": False, "retest_pass": False},
    "fixing": {"owner": True, "evidence": False, "retest_pass": False},
    "pending_retest": {"owner": True, "evidence": True, "retest_pass": False},
    "closed": {"owner": True, "evidence": True, "retest_pass": True},
}

_ORDER = ["open", "fixing", "pending_retest", "closed"]


def _now():
    return time.time()


def _normalize_reason(reason):
    if isinstance(reason, dict):
        return {"type": reason.get("type", "other"), "detail": reason.get("detail", "")}
    return {"type": "other", "detail": str(reason)}


def _find_master(vuln, project):
    """同项目同漏洞编号的未合并工单，重复开单时合并到它。"""
    if not vuln:
        return None
    for ticket in _tickets.values():
        if ticket["vuln"] == vuln and ticket["project"] == project and not ticket["merged_into"]:
            return ticket
    return None


def _snapshot(ticket):
    row = dict(ticket)
    row["evidence"] = list(ticket["evidence"])
    row["merged"] = list(ticket["merged"])
    row["retests"] = sorted(ticket["retests"], key=lambda r: r["time"])
    row["history"] = list(ticket["history"])
    return row


def _transition(current, target, operator, reason):
    source = current["status"]
    if source == "closed":
        raise ValueError("工单已关闭，不能再变更状态")
    if source == "merged":
        raise ValueError("工单已合并到 %s" % current["merged_into"])
    if target not in _WORKFLOW:
        raise ValueError("未知状态 %s" % target)
    if not operator:
        raise ValueError("状态变更需要操作人")
    forward = _ORDER.index(target) > _ORDER.index(source)
    if forward:
        if _ORDER.index(target) != _ORDER.index(source) + 1:
            raise ValueError("不能从 %s 直接流转到 %s" % (source, target))
        cond = _WORKFLOW[target]
        if cond["owner"] and not current["owner"]:
            raise ValueError("进入 %s 需要责任人" % target)
        if cond["evidence"] and not current["evidence"]:
            raise ValueError("进入 %s 需要修复证据" % target)
        if cond["retest_pass"] and not any(r["result"] == "pass" for r in current["retests"]):
            raise ValueError("关闭需要复测通过")
    else:
        if not reason:
            raise ValueError("退回必须填写原因")
    current["status"] = target
    current["changes"] += 1
    current["history"].append({
        "from": source,
        "to": target,
        "operator": operator,
        "reason": _normalize_reason(reason) if reason else None,
        "time": _now(),
    })


def _add_retest(current, retest, operator):
    if current["status"] != "pending_retest":
        raise ValueError("只有待复测状态才能提交复测结果")
    if not isinstance(retest, dict) or retest.get("result") not in ("pass", "fail"):
        raise ValueError("复测结果需要 pass 或 fail")
    current["retests"].append({
        "result": retest["result"],
        "by": retest.get("by") or operator,
        "note": retest.get("note", ""),
        "time": _now(),
    })
    if retest["result"] == "fail":
        _transition(current, "fixing", retest.get("by") or operator,
                    {"type": "retest_fail", "detail": retest.get("note", "")})


def update(ticket):
    if not isinstance(ticket, dict):
        raise ValueError("ticket 需要字典")
    ticket_id = ticket.get("id")
    if not ticket_id:
        raise ValueError("缺少工单编号")
    current = _tickets.get(ticket_id)
    if current is None:
        master = _find_master(ticket.get("vuln"), ticket.get("project"))
        current = {
            "id": ticket_id,
            "vuln": ticket.get("vuln"),
            "project": ticket.get("project"),
            "status": "open",
            "owner": None,
            "evidence": [],
            "merged_into": None,
            "merged": [],
            "retests": [],
            "changes": 0,
            "history": [],
        }
        _tickets[ticket_id] = current
        if master is not None:
            current["status"] = "merged"
            current["merged_into"] = master["id"]
            master["merged"].append(ticket_id)
            current["history"].append({
                "from": None, "to": "merged",
                "operator": ticket.get("operator"),
                "reason": {"type": "duplicate", "detail": "重复漏洞，合并到 %s" % master["id"]},
                "time": _now(),
            })
            return _result(current)
        current["history"].append({
            "from": None, "to": "open",
            "operator": ticket.get("operator"), "reason": None, "time": _now(),
        })
    elif ticket.get("vuln") and ticket["vuln"] != current["vuln"]:
        raise ValueError("工单 %s 已对应漏洞 %s" % (ticket_id, current["vuln"]))

    if current["status"] == "merged":
        raise ValueError("工单已合并到 %s" % current["merged_into"])
    if ticket.get("owner"):
        current["owner"] = ticket["owner"]
    if ticket.get("evidence"):
        current["evidence"].append(ticket["evidence"])
    if ticket.get("retest"):
        _add_retest(current, ticket["retest"], ticket.get("operator"))
    target = ticket.get("status")
    if target and target != current["status"]:
        _transition(current, target, ticket.get("operator"), ticket.get("reason"))
    return _result(current)


def _result(current):
    return {
        "ticket": current["id"],
        "vuln": current["vuln"],
        "status": current["status"],
        "owner": current["owner"],
        "evidence": list(current["evidence"]),
        "merged_into": current["merged_into"],
        "retests": sorted(current["retests"], key=lambda r: r["time"]),
        "changes": current["changes"],
    }


def _is_stuck(ticket):
    if ticket["owner"] is None:
        return True
    if ticket["status"] == "pending_retest" and not ticket["retests"]:
        return True
    return False


def report(project=None):
    rows = [
        ticket
        for ticket in _tickets.values()
        if project is None or ticket["project"] == project
    ]
    stuck = []
    duplicates = []
    reject_reasons = {}
    for row in rows:
        if row["merged_into"]:
            duplicates.append({"ticket": row["id"], "merged_into": row["merged_into"]})
        elif row["status"] != "closed" and _is_stuck(row):
            stuck.append(row["id"])
        for entry in row["history"]:
            reason = entry.get("reason")
            if reason and entry["to"] != "merged":
                reject_reasons[reason["type"]] = reject_reasons.get(reason["type"], 0) + 1
    return {
        "project": project,
        "tickets": [_snapshot(row) for row in rows],
        "stuck": sorted(stuck),
        "duplicates": duplicates,
        "reject_reasons": reject_reasons,
    }
