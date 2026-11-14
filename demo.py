from crawlbackoff import budget, signal

print(signal("shop", 429))
print(budget("shop"))

# TODO: 第二段演示（实现后补）
