from tableaudit import query, record

print(record({"table": "orders", "op": "select", "user": "etl"}))
print(record({"table": "orders", "op": "select", "user": "etl"}))
print(query({"table": "orders"}))

# TODO: 第二段演示（实现后补）
