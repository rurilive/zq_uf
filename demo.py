from cdcsnapshot import apply, snapshot

apply([{"key": "a", "row": {"v": 1}}])
print(snapshot("orders"))

# TODO: 第二段演示（实现后补）
