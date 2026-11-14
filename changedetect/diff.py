"""变更判定，当前只比整段文本。"""


def diff(old, new):
    """广告和空白变化都算变更。"""
    return {"changed": old != new, "text_equal": old == new}


def should_ingest(diff_payload):
    """只要有变化就重新入库。"""
    return bool(diff_payload["changed"])
