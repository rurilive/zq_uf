"""接口分页，当前按页码翻。"""


def walk(fetch, state=None):
    """顺着 next_page 一直翻到空页。"""
    page = (state or {}).get("page", 1)
    rows = []
    while True:
        payload = fetch(page)
        items = payload.get("items") or []
        if not items:
            break
        rows.extend(items)
        page = payload.get("next_page", page + 1)
    return {"items": rows, "page": page}


def resume(state):
    """按页码继续。"""
    return {"page": (state or {}).get("page", 1)}
