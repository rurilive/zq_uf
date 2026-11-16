from idormatrix import report, verify

API = {
    "name": "order.detail",
    "owner": "u1",
    "low_privilege": ["u3"],
    "cases": [
        {"as": "u1", "resource_owner": "u1", "status": 200},
        {"as": "u2", "resource_owner": "u1", "status": 200},
        {"as": "u3", "resource_owner": "u1", "status": 403},
        {"as": "guest", "resource_owner": "u1", "status": 500},
    ],
}

result = verify(API)
print(result["combos"])
print(result["coverage"])
print(result["failed"])
print(result["errors"])
print(report())

# 第二段演示：修复他人越权后重跑，回归差异按接口统计，历史结论保留
FIXED = dict(API, cases=[dict(c, status=403) if c["as"] == "u2" else dict(c) for c in API["cases"]])
again = verify(FIXED)
print(again["regression"])
body = report()
print(body["regression"])
print(body["history"]["order.detail"])
print(body["rerun"])
