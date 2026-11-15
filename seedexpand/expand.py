"""种子链接扩散，当前只做一跳且不设上限。"""


def expand(page, depth):
    """把页面上的链接全部作为新增目标返回。"""
    links = list(page.get("links", []))
    return {"depth": depth, "added": links, "truncated": []}


def budget(site):
    """当前没有每层上限，返回空预算。"""
    return {"site": site, "limit": None, "used": 0}
