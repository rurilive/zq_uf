from reportcheck import coverage, report

# 一轮扫描的登记结果：命中、未命中、跳过都写进来，未测的类型也留在清单里。
coverage(
    {
        "name": "GET /orders",
        "environment": "test",
        "version": "1.0",
        "expected": ["sqli", "xss", "ssrf", "csrf"],
        "types": {
            "sqli": "hit",
            "xss": "miss",
            "ssrf": {"result": "skip", "reason": "权限不足"},
        },
    }
)
coverage(
    {
        "name": "GET /orders",
        "environment": "prod",
        "version": "1.0",
        "types": {"sqli": "hit", "xss": "hit", "ssrf": "skip:环境不支持"},
    }
)

# 新接口上线，只声明应测类型，自动进入待测清单。
coverage(
    {
        "name": "POST /pay",
        "environment": "test",
        "version": "1.0",
        "expected": ["sqli", "csrf", "idor"],
    }
)

# 2.0 上线后线上只复测了 sqli，覆盖下降单独提示。
downgraded = coverage(
    {
        "name": "GET /orders",
        "environment": "prod",
        "version": "2.0",
        "types": {"sqli": "hit"},
    }
)

print("线上 2.0 覆盖下降:", downgraded["coverage_drop"])
print("线上 2.0 已覆盖:", [item["type"] for item in downgraded["covered"]])
print("线上 2.0 未覆盖:", downgraded["missing"])

# 补测后覆盖回到同一口径，下降提示消失。
backfilled = coverage(
    {
        "name": "GET /orders",
        "environment": "prod",
        "version": "2.0",
        "types": {"xss": "miss", "ssrf": "skip:权限不足"},
    }
)
print("补测后是否仍提示下降:", backfilled["dropped"])
print("补测后覆盖:", backfilled["coverage"])

summary = report("GET /orders")
print("按接口查看行数:", summary["count"])
for row in summary["rows"]:
    print(
        row["environment"],
        "已覆盖", [item["type"] for item in row["covered"]],
        "未覆盖", row["missing"],
        "跳过", row["skip_reasons"],
        "待测", row["pending_new"],
    )
print("新增待测接口:", report()["pending_new_apis"])
