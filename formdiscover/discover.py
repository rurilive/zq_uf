"""表单发现，当前只认常见可见字段。"""

VISIBLE_TYPES = {"text", "password", "email", "search"}


def discover(page):
    """解析单页表单，隐藏字段和按钮都被丢掉。"""
    inputs = page.get("inputs") or []
    fields = [item for item in inputs if item.get("type") in VISIBLE_TYPES]
    return {
        "action": page.get("action", ""),
        "method": (page.get("method") or "get").lower(),
        "fields": fields,
    }
