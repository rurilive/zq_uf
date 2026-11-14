"""变更应用，当前按到达顺序覆盖。"""

_rows = {}


def reset():
    _rows.clear()


def apply(events):
    """后到直接覆盖，不看版本。"""
    for event in events:
        _rows[event["key"]] = event["row"]
    return len(events)


def snapshot():
    return dict(_rows)
