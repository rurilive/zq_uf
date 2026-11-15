import logging

import retryflow.flow as flow
from retryflow import outcome, records, report, retry

print(retry("fetch-1", "shop"))
print(retry("fetch-1", "shop"))
print(retry("fetch-1", "shop"))
print(outcome("fetch-1"))

# 第二段：模拟站点熔断 -> 重试等待 -> 阶梯放量恢复
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

clock = [1000.0]
flow._now = lambda: clock[0]  # 演示用假时钟，避免真实等待 30 秒恢复期

site = "mall"
for i in range(3):
    print(report("seed-%d" % i, site, False, error="timeout"))  # 连续超时，触发熔断
print(retry("fetch-2", site))       # 熔断中：wait，不消耗重试次数
clock[0] += 31                      # 过了恢复期，进入半开
print(retry("fetch-2", site))       # 阶梯第 1 阶：放行 1 个
print(retry("fetch-3", site))       # 阶梯限流：wait
for i in range(6):
    print(report("probe-%d" % i, site, True))  # 连续成功，走满阶梯后闭环
print(retry("fetch-3", site))       # 已闭环：正常 retry，预算回满
print(outcome("fetch-2"))
print("records:", len(records()))
