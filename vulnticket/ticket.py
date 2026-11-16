"""漏洞工单状态流转，当前按传入值直接改状态。"""

_tickets = {}


def update(ticket):
    if not isinstance(ticket, dict):
        raise ValueError("ticket 需要字典")
    ticket_id = ticket.get("id")
    if not ticket_id:
        raise ValueError("缺少工单编号")
    current = _tickets.get(ticket_id)
    if current is None:
        current = {
            "id": ticket_id,
            "vuln": ticket.get("vuln"),
            "project": ticket.get("project"),
            "status": "open",
            "owner": None,
            "evidence": [],
            "merged_into": None,
            "retests": [],
        }
        _tickets[ticket_id] = current
    current["status"] = ticket.get("status", current["status"])
    current["owner"] = ticket.get("owner", current["owner"])
    if ticket.get("evidence"):
        current["evidence"].append(ticket["evidence"])
    return {
        "ticket": ticket_id,
        "vuln": current["vuln"],
        "status": current["status"],
        "owner": current["owner"],
        "evidence": list(current["evidence"]),
        "merged_into": None,
        "retests": list(current["retests"]),
        "changes": 0,
    }


def report(project=None):
    rows = [
        ticket
        for ticket in _tickets.values()
        if project is None or ticket["project"] == project
    ]
    return {
        "project": project,
        "tickets": [dict(row) for row in rows],
        "stuck": [],
        "duplicates": [],
    }
