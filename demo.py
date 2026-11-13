from driftcheck import backtest, check

# 旧口径兼容：无 history 时仍按固定阈值
print(check("orders", {"value": 130, "baseline": 100}))
print(check("orders", {"value": 105, "baseline": 100}))

# 第一组：正常波动，历史基线 100 上下，当前值在波动范围内，不告警
normal = [98, 102, 101, 99, 100, 103, 97, 101, 99, 102, 100, 98, 101, 100]
print("normal:", check("orders", {"value": 103, "history": normal}))

# 第二组：周期数据，周末高峰约 200、工作日约 100
# 周六 205 与同周期（前 4 个周六）一致，不告警；周六 320 偏离同周期基线，告警
weekly = [100, 102, 98, 101, 99, 200, 205] * 3 + [100, 102, 98, 101, 99]
print("weekend ok:", check("orders", {"value": 203, "history": weekly}))
print("weekend bad:", check("orders", {"value": 320, "history": weekly}))

# 第三组：缓慢漂移，每天涨约 3%，单日看都不夸张，趋势斜率触发告警
drifting = [round(100 * 1.03 ** i, 2) for i in range(14)]
print("slow drift:", check("orders", {"value": round(100 * 1.03 ** 14, 2), "history": drifting}))

# 回测：用 60 天历史回放阈值效果，含一次突增和一段缓慢漂移
series = [100 + (i % 5) - 2 for i in range(30)] + [160] + [100 + (i % 5) - 2 for i in range(29)]
print("backtest:", backtest("orders", series))
