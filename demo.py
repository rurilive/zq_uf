from datasearch import search

for query in ("orders", "订单明细", "user"):
    print(query, [t["name"] for t in search(query)])

# TODO: 第二段演示（实现后补）