from datacontract2 import check

old = {"fields": {"id": "int", "name": "text"}}
new = {"fields": {"id": "text"}}
print(check(old, new))

# TODO: 第二段演示（实现后补）
