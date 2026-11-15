from dnscache2 import report, resolve

print(resolve("shop"))
print(report("shop", "10.0.0.1", True, 0.12))

# TODO: 第二段演示（实现后补）
