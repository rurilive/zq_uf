from fieldsource import extract

page = {"title": "机械键盘", "price_text": "299.00", "price_meta": "299"}

rules = [
    {"field": "title", "get": lambda p: p.get("title")},
    {"field": "price", "get": lambda p: p.get("price_text")},
    {"field": "price", "get": lambda p: p.get("price_meta")},
]

print(extract(page, rules))

# TODO: 第二段演示（实现后补）
