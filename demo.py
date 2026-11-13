import json

from fieldsource import extract_many

pages = [
    {"id": "p1", "title": "机械键盘", "price_text": "价格 299.00 元", "price_meta": "299"},
    {"id": "p2", "title": "机械键盘", "price_text": "促销 279.00 元"},
]

rules = [
    {"field": "title", "get": lambda p: p.get("title"), "id": "title", "version": "1.0"},
    {"field": "price", "get": lambda p: (p.get("price_text") or "").split(" ")[1] or None,
     "id": "price-text", "name": "价格文本", "version": "2.0", "priority": 10},
    {"field": "price", "get": lambda p: p.get("price_meta"),
     "id": "price-meta", "name": "价格meta", "version": "1.3", "priority": 5},
]

result = extract_many(pages, rules)
print("主值:", dict(result))
print("price 候选:")
for c in result.sources["price"]["candidates"]:
    print(" ", c["rule"]["id"], c["rule"]["version"], "page=", c["page"],
          "value=", c["value"], "pos=", c["position"])
print("序列化(截断片段):", json.dumps(result.to_dict(max_snippet=12), ensure_ascii=False))
