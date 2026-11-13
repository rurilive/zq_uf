# slapredict

现状：所有任务共用一个历史平均值，预测区间宽度为零。

问题：数据量翻倍就延误，不同任务混算偏差大，没有区间不敢排期。

目标：按任务类型和数据量拟合，近期样本加权，剔除异常并可回测。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 特征与参数
- `predict(task, size)` 按任务类型加权最小二乘拟合 `duration = a + b * size`，返回 `{eta, low, high, n, outliers}`
- 近期样本权重更高：权重 `DECAY ** 距最新条数`，`DECAY = 0.9`
- 异常剔除：Theil-Sen 稳健拟合后，残差超 `OUTLIER_Z = 3.0` 倍 MAD 标准差的样本被标记（`outliers()`）并剔除
- 区间：`eta ± Z_INTERVAL(1.96) × 残差标准误`（含杠杆修正）；类型样本不足 `MIN_SAMPLES = 3` 时退化为全局均值、区间宽度为零
- 状态可序列化：`to_dict()/from_dict()`、`dumps()/loads()`，重启后复现预测
- 非法 task/size/duration、空样本、坏状态一律抛 `ValueError`，不静默跳过

## 回测口径
- `backtest(task)` 走前向（walk-forward）：仅用前 i 条样本拟合，预测第 i 条，不用未来数据
- 每条记录含 `size/actual/eta/low/high/hit`，`hit` 表示实际耗时落在预测区间内
- 汇总 `mae`（平均绝对误差）与 `coverage`（区间命中率 = 命中数 / 总数）
- 样本数不足 `MIN_SAMPLES + 1` 或任务无样本时抛 `ValueError`
