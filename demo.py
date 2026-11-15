"""两组演示：正常波动（周期对齐，不告警）与缓慢漂移 + 突刺（告警、去重、回测）。"""

import json

from qualitytrend import backtest, check, export, feedback, reset, trend


def show(result):
    print(json.dumps(result, ensure_ascii=False))


print("== 第一组：正常波动（周一冲高是周期常态）==")
reset()
for week in range(4):
    for weekday in range(7):
        value = 0.050 if weekday == 0 else 0.010 + 0.001 * ((week + weekday) % 3)
        check("orders", {"value": value, "day": weekday})
show(check("orders", {"value": 0.050, "day": 0}))  # 周一冲高，同周期基线覆盖，不告警
show(check("orders", {"value": 0.011, "day": 2}))
print(trend("orders"))

print("== 第二组：缓慢漂移 + 突刺 ==")
reset()
for v in [0.010, 0.012] * 5:
    check("payments", {"value": v})
for v in (0.0135, 0.0140, 0.0145):  # 缓慢漂移，连续 3 个窗口确认后告警
    show(check("payments", {"value": v}))
show(check("payments", {"value": 0.05}))  # 突刺，偏离倍数告警
show(check("payments", {"value": 0.06}))  # 同原因连续告警，去重
feedback("payments")  # 把最近一次告警标记为误报，反馈给回测
print(backtest("payments"))  # 回测：告警数、误报数、命中率
print(export("payments", path="trend_export.json")["trend"])  # 导出趋势结果
