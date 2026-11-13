"""页码递增的翻页实现。"""


def walk(fetch_page):
    page = 1
    items = []
    while True:
        batch = fetch_page(page)
        if not batch:
            break
        items.extend(batch)
        page += 1
    return items
