from sitemapdiff import diff, snapshot

old = snapshot({"links": ["a", "b"], "depth": 1})
new = snapshot({"links": ["a", "c"], "depth": 1})
print(diff(old, new))

# TODO: 第二段演示（实现后补）
