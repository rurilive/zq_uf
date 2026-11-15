from cdcbatch import apply, resume

print(apply({"id": "b1", "rows": [{}, {}, {"bad": True}]}))
print(resume("b1"))

# TODO: 第二段演示（实现后补）
