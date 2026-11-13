# siteweight

采集站点动态权重调度器，纯 Python 标准库。按历史成功率和更新频率动态调权，
连续失败降权、恢复后回升，低权站点有保底配额，避免饿死。

## 接口

- `next()` / `next_site()`：返回下一个调度站点（两者等价），并更新等待统计。
- `feedback(site, result)`：回传采集结果，`result` 必须是 `bool`，每次回传后立即重算权重，返回累计反馈条数。
- `weights()`：各站点当前有效权重。
- `weight_changes()`：权重变化事件，字段为 `site / old / new / reason / pick`。
- `stats()`：选中次数、等待时间、窗口样本、成功率、保底强制次数等统计。
- `reset()`：清空全部历史状态，回到等权冷启动。

## 权重公式

每个站点保留最近 `WINDOW_SIZE=20` 条反馈的滑动窗口。样本数不足
`MIN_SAMPLES=3` 时不参与调权，权重保持 `BASE_WEIGHT=10`，因此冷启动阶段
严格等权轮转，与旧版行为一致。

样本充足时按下列公式计算，再截断到 `[MIN_WEIGHT, BASE_WEIGHT]`（即 `[2, 10]`）：

```
success_rate = 窗口内成功数 / 窗口样本数
freq_ratio   = 本站窗口样本数 / 所有站点最大窗口样本数   # 无反馈时取 1.0
score        = SUCCESS_WEIGHT * success_rate + FREQUENCY_WEIGHT * freq_ratio
weight       = round(BASE_WEIGHT * score)
             = round(10 * (0.7 * success_rate + 0.3 * freq_ratio))
```

成功率占 70%（`SUCCESS_WEIGHT=0.7`），更新频率占 30%（`FREQUENCY_WEIGHT=0.3`）：
成功率高且更新频繁的重点站拿满 10，长期失败的站点向 `MIN_WEIGHT=2` 靠拢。

## 降权与回升

- 连续失败达到 `FAIL_THRESHOLD=3` 次进入降权状态，权重再乘
  `FAIL_PENALTY=0.5`，事件原因为 `consecutive_failures`。
- 降权状态下连续成功达到 `RECOVER_THRESHOLD=2` 次解除降权，事件原因为
  `recovered`，权重按当前窗口公式重新计算并自然回升。
- 连续成功被任意一次失败打断后重新计数，避免抖动回升。

## 保底口径（避免饿死）

双重保底，均为硬性可验证口径：

- 软保底：权重永远不低于 `MIN_WEIGHT=2`。3 个站点时低权站点保底份额约为
  `2 / (10 + 10 + 2) ≈ 9%`，即每 30 轮至少被选中 4 次（由测试锁定）。
- 硬保底：任一站点连续等待达到 `STARVATION_LIMIT = 2 * 站点数 = 6` 轮时，
  下一轮强制选中等待最久的站点（多个并列时按 `SITES` 顺序取第一个），
  计入 `stats()['starvation_picks']`，因此任何站点的 `max_wait <= 6`。

调度算法为平滑加权轮转（SWRR）：每轮累加各自权重，选当前值最大的站点并
减去权重总和；权重相等时退化为按 `SITES` 固定顺序轮转。

## 等待时间口径

等待时间以“调度轮次”为单位：站点本次被选中时，距上一次被选中经过的轮数
（首次被选中即从第 1 轮算起）。`stats()` 中：

- `max_wait`：历史最长等待轮数；`avg_wait`：平均等待轮数。
- `current_wait`：截至当前已连续等待的轮数，被选中时归零。
- `picks`：累计被选中次数；`total_picks`：总调度轮数。

## 异常路径

错误一律显式抛出，不静默跳过：

- `feedback` 的 `result` 非 `bool`：抛 `TypeError`，且不写入任何状态。
- `feedback` 的站点不在 `SITES`：抛 `UnknownSiteError`。
- `SITES` 为空时调用 `next()`：抛 `SiteWeightError`。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
