from rowversion import get, update

update("row-1", {"amount": 2}, 2)
update("row-1", {"amount": 1}, 1)
print(get("row-1"))

# TODO: 第二段演示（实现后补）
