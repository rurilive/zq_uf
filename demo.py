from schemadiff import diff

print(diff({"fields": [{"name": "id"}]}, {"fields": [{"name": "id"}, {"name": "note"}]}))

# TODO: 第二段演示（实现后补）
