# queuefresh

采集队列的新鲜度调度：按新鲜度衰减 + 等待提权动态排序，纯 Python 标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 分数公式

分数小的先出，同分（差值 < 1e-6）按入队顺序 FIFO：

```
score = priority
      + stale_weight * (1 - 2 ** (-age / half_life))
      - boost_step * floor(wait / boost_threshold)
      + expired_penalty          # 仅当 now > deadline
```

`age = now - published_at`，`wait = now - 入队时刻`。陈腐罚分有上界
（stale_weight），提权线性增长，因此等待足够久必然反超，不会饿死。
已过 deadline 的任务罚分沉底，但不会被丢弃。

## 参数与阈值

- `priority`：任务基准优先级，默认 5，越小越重要
- `published_at` / `deadline`：任务时间戳（秒），默认入队时刻 / 不过期
- `half_life`：新鲜度半衰期秒数，默认 3600，须 > 0
- `boost_threshold`：提权阈值秒数，默认 600，每等待满一次 boosts +1，须 > 0
- `boost_step`：每次提权减去的分数，默认 1.0，须 >= 0
- `stale_weight`：陈腐罚分上限，默认 4.0；`expired_penalty` 过期罚分，默认 1000.0
- `clock`：时间源，默认 `time.time`，测试可注入假时钟

## 输出与错误

- `pop()` 返回任务副本，附带 `score`（当前分数）和 `boosts`（提权次数）；空队列返回 None
- `Queue.snapshot()` 按出队顺序返回全队列的 `{task, score, boosts}`
- 非法输入（非 dict 任务、非数字字段、deadline 早于 published_at、非法构造参数）
  抛出 `TypeError` / `ValueError`，不会静默跳过
