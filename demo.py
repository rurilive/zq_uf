from idormatrix import report, verify

API = {
    "name": "order.detail",
    "owner": "u1",
    "cases": [
        {"as": "u1", "resource_owner": "u1", "status": 200},
        {"as": "u2", "resource_owner": "u1", "status": 200},
        {"as": "u2", "resource_owner": "u1", "status": 403},
        {"as": "guest", "resource_owner": "u1", "status": 500},
    ],
}

result = verify(API)
print(result["checks"])
print(result["failed"])
print(report())

# TODO: 第二段演示（实现后补）
