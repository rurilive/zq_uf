from etagcache import headers, store

print(headers("https://shop/p"))
print(store("https://shop/p", {"status": 200, "body": "full page"}))
print(store("https://shop/p", {"status": 304, "body": ""}))

# TODO: 第二段演示（实现后补）
