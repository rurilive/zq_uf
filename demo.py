from crawlcap import acquire, report

for _ in range(5):
    print(acquire("shop"))
print(report("shop", True, 0.31))

# TODO: 第二段演示（实现后补）
