"""演示：同一网段两个目标，各自按反馈独立调速，中断后续扫。"""

from covmatrix import report, scan

T0 = 1_000_000

# 10.0.0.5 被限流：降速并保留限流原文，冷却后健康轮逐步提回
print(scan({"host": "10.0.0.5", "progress": 100, "now": T0}))
print(scan({"host": "10.0.0.5", "throttle": "429 too many requests",
            "progress": 140, "now": T0 + 30}))
print(scan({"host": "10.0.0.5", "now": T0 + 90}))

# 同网段另一目标 10.0.0.6 反馈健康，速率不受邻居影响
print(scan({"host": "10.0.0.6", "latency_ms": 120, "progress": 200, "now": T0}))

# 10.0.0.7 连续限流：暂停本轮，退避后从 resume 位置继续
for i in range(3):
    print(scan({"host": "10.0.0.7", "throttle": "429 again",
                "now": T0 + i * 10}))
print(scan({"host": "10.0.0.7", "progress": 50, "now": T0 + 40}))   # 退避中，保持暂停
print(scan({"host": "10.0.0.7", "progress": 50, "now": T0 + 4000}))  # 退避结束，续扫

# 运营按目标查看：当前速率、调整次数、限流提示、续扫位置与调整记录
import json
print(json.dumps(report("10.0.0.5"), ensure_ascii=False, indent=2))
