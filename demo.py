from tablelease import acquire, release

print(acquire("orders", "etl-1", 30))
print(acquire("orders", "etl-2", 30))
print(release("orders", "etl-2"))

# TODO: 第二段演示（实现后补）
