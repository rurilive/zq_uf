# siteweight

现状：固定顺序等权轮转，反馈只记录不改变权重。

问题：重点站点更新慢，流量变化后不及时，低权站点容易超时。

目标：按成功率和更新频率动态调权，降权可回升，低权有保底配额。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 权重公式

反馈数不足 `min_samples` 时权重恒为 1.0（等权轮转）；达到后：

    score  = success_ratio * 成功率 + update_ratio * 更新率   （归一化后 ∈ [0, 1]）
    weight = min_weight + (max_weight - min_weight) * score * decay ** 连续失败次数

成功一次即清零连续失败计数，权重自动回升；选站用平滑加权轮转。

## 保底与参数

保底口径：任意站点连续 `guarantee_window` 次未被选中即强制选中，
因此单站最大等待不超过 `guarantee_window`，每 window 次至少被采一次。

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `min_samples` | 3 | 反馈少于此数不调权 |
| `min_weight` / `max_weight` | 0.1 / 3.0 | 权重上下限 |
| `success_ratio` / `update_ratio` | 0.7 / 0.3 | 成功率与更新率占比 |
| `decay` | 0.5 | 连续失败衰减因子 |
| `guarantee_window` | 10 | 保底窗口（不小于站点数） |

异常路径：未知站点抛 `KeyError`，非法结果类型抛 `TypeError`，
非法参数抛 `ValueError`，不静默跳过。
