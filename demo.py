"""用历史样本演示按任务类型的耗时预测、异常剔除与回测。"""

from slapredict import add_sample, backtest, outliers, predict, reset

reset()

# 默认状态：没有分类型样本，退化为全局均值，区间宽度为零
print(predict("etl", 1000))
print(predict("report", 100000))

# 历史样本：etl 近似 60 + 0.1 * size，report 近似 20 + 0.002 * size
for size, duration in [(1000, 158), (2000, 263), (3000, 357), (4000, 462), (5000, 558)]:
    add_sample("etl", size, duration)
for size, duration in [(10000, 41), (50000, 119), (100000, 221)]:
    add_sample("report", size, duration)
add_sample("etl", 5000, 5000)  # 异常耗时，会被标记并剔除

# 按任务类型 + 数据量预测，输出预计完成时间与区间
print(predict("etl", 6000))
print(predict("report", 100000))
print("outliers:", outliers("etl"))

# 走前向回测：MAE 与区间覆盖率
result = backtest("etl")
print("backtest: count=%d mae=%.2f coverage=%.2f" % (result["count"], result["mae"], result["coverage"]))
