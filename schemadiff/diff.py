"""结构差异，当前只看字段名。"""


def diff(old, new):
    old_fields = [f["name"] for f in old.get("fields") or []]
    new_fields = [f["name"] for f in new.get("fields") or []]
    return {
        "added": [name for name in new_fields if name not in old_fields],
        "removed": [name for name in old_fields if name not in new_fields],
    }


def impact(diff_payload):
    """没有血缘，影响面为空。"""
    return []
