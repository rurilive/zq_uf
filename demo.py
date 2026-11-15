from fieldpatch import patch, report

print(patch({"title": "x"}, {"required": ["title", "price"]}))
print(report())

# TODO: 第二段演示（实现后补）
