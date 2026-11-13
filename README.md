# fairqueue

按站点权重公平调度的采集任务队列，纯 Python 标准库。

## 调度口径

- 每个站点一个先进先出子队列，`push(task)` 按 `task["site"]` 入队（缺省 `"default"`）。
- 调度采用平滑加权轮询：长期出队比例约等于站点权重比；同权重按队头任务的入队时间，等待最久的站点先出队。
- 重试任务（`task["retry"]` 为真）回到原站点队列尾部，不再插到全局队尾。
- `quota` 是每站点最大在途任务数：达到配额的站点本轮被跳过，`done(task)` 释放名额；没有可调度任务时 `next()` 返回 `None`。

## 参数与阈值

| 参数 | 含义 | 合法取值 | 非法时报错 |
| --- | --- | --- | --- |
| `weights` / `set_weight` | 站点权重，缺省 1 | 正数 int/float | 非数字 `TypeError`，<=0 `ValueError` |
| `quota` | 每站点在途配额 | 正整数或 `None`（不限） | 非 int `TypeError`，<1 `ValueError` |
| `task` | 任务 | dict，`site` 为非空字符串 | 否则 `TypeError` / `ValueError` |
| `clock` | 计时源（测试注入用） | 可调用对象，缺省 `time.monotonic` | — |

`done(task)` 对不在在途集合中的任务抛 `KeyError`，不静默跳过。

## 统计

`stats()` 返回每站点：`pending`（在队数）、`in_flight`（在途数）、`dequeued`（出队数）、`retries`（重试入队数）、`total_wait` / `avg_wait` / `max_wait`（等待秒数）。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

demo 第一段：big 权重 2、small 权重 1 混合提交 9 条，出队顺序 `b1 s1 b2 b3 s2 b4 b5 s3 b6`，两站平均等待均为 9；第二段：`quota=1`，慢站在途占满后 `next()` 返回 `None`，`done()` 释放名额后继续出队，顺序 `slow1 fast1 None fast2 None slow2`。
