from reportcheck import coverage, report

print(coverage({"name": "GET /orders", "environment": "test", "types": {"sqli": "hit", "xss": "miss"}}))
print(coverage({"name": "GET /orders", "environment": "prod", "types": {"sqli": "hit"}}))
print(report("GET /orders")["count"])

# TODO: 第二段演示（实现后补）
