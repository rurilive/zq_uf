import tempfile

from proxyburn import store
from proxyburn import CONFIG, history, report, status, use

store.STATE_DIR = tempfile.mkdtemp(prefix="proxyburn-demo-")
CONFIG["low_traffic_hours"] = None  # 演示不限制低流量窗口
CONFIG["probe_interval_sec"] = 0

print(use("proxy-a"))
print(report("proxy-a", False))

# 第二段：连续失败熔断 → 低频探测 → 阶梯恢复
for _ in range(2):
    print(report("proxy-a", False))          # 达到阈值，熔断
print(use("proxy-a"))                        # 探测间隔已到，放行一次探测，进入半开第 1 级
print(use("proxy-a"))                        # 第 1 级只放 1 个在途，不放满
for _ in range(4):
    print(report("proxy-a", True))           # 连续成功，逐级恢复
print(status("proxy-a"))                     # 已恢复 closed
print(history("proxy-a"))                    # 审计：tripped/probe_start/stage_up/recovered
