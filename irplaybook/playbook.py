"""事件处置清单，当前按模板把步骤列出来。"""

_TEMPLATES = {
    "ransomware": ["isolate", "collect", "cleanup", "restore"],
    "phishing": ["quarantine", "collect", "notify"],
    "intrusion": ["isolate", "collect", "cleanup", "review"],
}

_EVENTS = {}


def reset():
    """清空已开单的事件。"""
    _EVENTS.clear()


def open(event):
    """按事件类型取步骤模板，把调用方标过的步骤记成完成。"""
    if not isinstance(event, dict):
        raise ValueError("event must be a dict")
    event_id = event.get("id")
    kind = event.get("type")
    if not event_id or not kind:
        raise ValueError("event needs id and type")
    if kind not in _TEMPLATES:
        raise ValueError("unknown event type")
    done = list(event.get("done", []))
    steps = []
    for name in _TEMPLATES[kind]:
        steps.append({"name": name, "state": "done" if name in done else "pending"})
    _EVENTS[event_id] = {
        "id": event_id,
        "type": kind,
        "operator": event.get("operator", ""),
        "steps": steps,
    }
    return {
        "id": event_id,
        "type": kind,
        "steps": len(steps),
        "done": sum(1 for step in steps if step["state"] == "done"),
    }


def timeline(event_id):
    """按模板顺序返回步骤，附上空的操作记录。"""
    if event_id not in _EVENTS:
        raise ValueError("unknown event")
    event = _EVENTS[event_id]
    return {
        "id": event_id,
        "type": event["type"],
        "steps": [dict(step) for step in event["steps"]],
        "records": [],
        "leftover": [],
        "closable": True,
    }
