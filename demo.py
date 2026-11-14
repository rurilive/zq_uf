from tablelock import acquire, release

print(acquire("orders", "job-a", 30))
print(acquire("orders", "job-b", 30))
print(release("orders", "job-a"))

# TODO: 第二段演示（实现后补）
