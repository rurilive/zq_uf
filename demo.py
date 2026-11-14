from crawlshard import elect, write

elect("shard-1", "m1")
print(elect("shard-1", "m3"))
print(write("shard-1", "m1", {"id": 1}))

# TODO: 第二段演示（实现后补）
