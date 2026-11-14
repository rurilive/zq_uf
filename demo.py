from proxyrotate import acquire, release

for _ in range(3):
    print(acquire("session-1"))
print(release("session-1", "10.0.0.1:80", False))

# TODO: 第二段演示（实现后补）
