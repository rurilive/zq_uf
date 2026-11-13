from reqcache import clear, get, put, stats

clear()
put("http://shop/1", {"status": 200, "body": "ok"})
print(get("http://shop/1"))
print(get("http://shop/2"))
print(stats())

# TODO: 第二段演示（实现后补）
