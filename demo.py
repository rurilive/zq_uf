from retryflow import outcome, retry

print(retry("fetch-1", "shop"))
print(retry("fetch-1", "shop"))
print(retry("fetch-1", "shop"))
print(outcome("fetch-1"))

# TODO: 第二段演示（实现后补）
