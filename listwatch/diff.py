"""列表页全量比对的最小实现。"""


def diff(old, new):
    old_titles = {item.get("title", "") for item in old}
    new_titles = {item.get("title", "") for item in new}
    return {
        "added": [item for item in new if item.get("title", "") not in old_titles],
        "removed": [item for item in old if item.get("title", "") not in new_titles],
    }
