"""分页抓取，当前只顺着next走。"""


def walk(fetch_page, start="http://shop/list?page=1", limit=100):
    """没有已访问集合，环路只靠页数上限截断。"""
    pages = []
    url = start
    while url and len(pages) < limit:
        page = fetch_page(url)
        pages.append(page["url"])
        url = page.get("next")
    return pages
