from depcycle import check

graph = {"a": ["b"], "b": ["c"], "c": ["a"]}
print("cycles", check(graph))

# TODO: 第二段演示（实现后补）
