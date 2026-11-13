from multisource import reconcile

A = [{"id": 1, "amount": 10}, {"id": 2, "amount": 20}]
B = [{"id": 1, "amount": 10}]
C = [{"id": 2, "amount": 20}, {"id": 3, "amount": 30}]

print(reconcile([A, B, C], {"key": "id"}))

# TODO: 第二段演示（实现后补）