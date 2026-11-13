from pagedigest import fetch_all, parse_page

PAGES = {
    1: {"items": [{"id": 1, "title": "机械键盘", "price": "299.00", "url": "http://shop/1"}]},
    2: {"items": [{"id": 2, "title": "显示器", "price": "1299.00", "url": "http://shop/2"}]},
}


def fetch(page):
    return PAGES.get(page, {"items": []})


items = fetch_all(fetch)
print("抓到", len(items), "条")
for item in items:
    print(item.item_id, item.title, item.price)
print(parse_page({"items": [{"id": 9, "title": "鼠标"}]}))

# TODO: 第二段演示（实现后补）
