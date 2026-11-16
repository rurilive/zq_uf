"""风控规则，当前写死在代码里。"""

_RULES = (
    {"id": "r1", "field": "amount", "op": ">=", "threshold": 1000, "action": "deny"},
    {"id": "r2", "field": "country", "op": "==", "value": "RU", "action": "deny"},
    {"id": "r3", "field": "channel", "op": "==", "value": "web", "action": "review"},
)

_EVENTS = {}


def reset():
    """清空事件记录。"""
    _EVENTS.clear()
    return {"events": 0}


def evaluate(event):
    """按代码里的顺序取第一条命中，取值不回传。"""
    if not isinstance(event, dict):
        raise ValueError("event must be a dict")
    event_id = event.get("id", "")
    conclusion = {"event": event_id, "action": "allow", "rule": "", "values": {}}
    for rule in _RULES:
        actual = event.get(rule["field"])
        if rule["op"] == ">=":
            if isinstance(actual, (int, float)) and actual >= rule["threshold"]:
                conclusion = {
                    "event": event_id,
                    "action": rule["action"],
                    "rule": rule["id"],
                    "values": {},
                }
                break
        elif rule["op"] == "==" and actual == rule["value"]:
            conclusion = {
                "event": event_id,
                "action": rule["action"],
                "rule": rule["id"],
                "values": {},
            }
            break
    _EVENTS[event_id] = conclusion
    return dict(conclusion)


def explain(rule_id):
    """只回规则字面条件，没有回放记录。"""
    for rule in _RULES:
        if rule["id"] == rule_id:
            if rule["op"] == ">=":
                condition = "%s %s %s" % (rule["field"], rule["op"], rule["threshold"])
            else:
                condition = "%s %s %s" % (rule["field"], rule["op"], rule["value"])
            return {
                "rule": rule_id,
                "condition": condition,
                "threshold": rule.get("threshold"),
                "replay": [],
            }
    return {"rule": rule_id, "condition": "", "threshold": None, "replay": []}
