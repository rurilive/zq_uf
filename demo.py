from contractdiff import check

print(check({"fields": {"id": "int", "name": "text"}}, {"fields": {"id": "text"}}))

# TODO: 第二段演示（实现后补）
