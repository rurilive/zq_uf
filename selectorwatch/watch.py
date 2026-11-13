"""选择器巡检，当前只统计非空字段总数。"""


def check(site, pages):
    """返回站点和命中总数，不区分字段。"""
    hits = 0
    for page in pages:
        for value in page.values():
            if value:
                hits += 1
    return {"site": site, "hits": hits, "pages": len(pages)}
