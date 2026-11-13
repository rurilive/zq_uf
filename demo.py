from datasetcache import clear, get, invalidate, put

clear()
put("orders:1", {"rows": 3}, now=100)
print(get("orders:1", now=101))
print(invalidate("orders"))
print(get("orders:1", now=101))

# TODO: 第二段演示（实现后补）