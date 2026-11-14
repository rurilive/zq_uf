from tablesync import snapshot, sync

sync("orders", [{"id": 1, "v": 1}, {"id": 2, "v": 2}])
sync("orders", [{"id": 1, "v": 3}])
print(snapshot())

# TODO: 第二段演示（实现后补）
