from crawllease import claim, renew

claim("task-1", "worker-a", 30)
claim("task-1", "worker-b", 30)
print("a still owns:", renew("task-1", "worker-a", 30))

# TODO: 第二段演示（实现后补）
