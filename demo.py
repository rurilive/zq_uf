from linkgraph import build, cycles

build({"a": ["b"], "b": ["a"]})
print("nodes", len(build({"a": ["b"], "b": ["a"]})))
print("cycles", cycles())

# TODO: 第二段演示（实现后补）
