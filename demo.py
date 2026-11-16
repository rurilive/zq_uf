from covmatrix import report, scan

print(scan({"host": "10.0.0.5"}))
print(scan({"host": "10.0.0.5", "throttle": "429 too many requests"}))
print(report()["targets"])

# 第二段：按目标反馈自适应调速
print(scan({"host": "10.0.0.6"}))
print(scan({"host": "10.0.0.6", "latency_ms": 2500}))          # 响应变慢，降速
print(scan({"host": "10.0.0.6"}))                              # 恢复，逐步提速
for _ in range(3):
    print(scan({"host": "10.0.0.7", "throttle": "429 too many requests"}))  # 连续限流，暂停本轮
print(report("10.0.0.7"))                                      # 单目标查看：速率、原因、续扫位置
print(report()["adjustments"])                                 # 全部调整记录：前后取值和触发原因
