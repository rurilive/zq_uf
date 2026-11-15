from auditquery import load, page, search

load([{"user": "etl", "op": "select"}, {"user": "bi", "op": "drop"}])
print(search({"user": "etl"}))
print(page({"offset": 0, "size": 1}))

# TODO: 第二段演示（实现后补）
