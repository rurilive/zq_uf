from etlconf import transform

ROWS = [{"order_id": "1", "amt": "10"}]
print(transform(ROWS, {"rename": {"order_id": "id"}}))

# TODO: 第二段演示（实现后补）
