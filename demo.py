from cdcordering import apply, snapshot

apply([{"key": "a", "row": {"v": 2}}, {"key": "a", "row": {"v": 1}}])
print(snapshot())

# TODO: 第二段演示（实现后补）
