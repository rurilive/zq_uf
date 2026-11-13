from syncconflict import merge

print(merge({"id": 1, "price": "1"}, {"id": 1, "price": "2"}))
print(merge({"id": 2, "deleted": True}, {"id": 2, "price": "9"}))

# TODO: 第二段演示（实现后补）