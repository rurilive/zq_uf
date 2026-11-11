# crawlflow

可恢复的增量采集调度器，纯标准库，状态持久化在本地 SQLite。

```bash
python3 demo.py                          # 模拟多站点采集 + 崩溃恢复
python3 -m unittest discover -s tests    # 全部测试
```

## 用法

```python
from crawlflow import Scheduler, SitePolicy, NetworkError

sched = Scheduler(
    "crawl.sqlite",                      # 状态库；":memory:" 为纯内存
    backoff_base=1.0, backoff_cap=300.0, # 指数退避：base * 2**(n-1)，封顶 cap
    max_attempts=3,                      # 网络错误最多尝试次数，用尽落死信
    sites={"slow.com": SitePolicy(max_concurrency=1, min_interval=2.0)},
    default_policy=SitePolicy(max_concurrency=2),
)

sched.add("https://a.com/p?id=1&utm_source=x", priority=10)  # 归一化去重
task = sched.next()                      # 优先级高者优先，同级先入队先出
sched.success(task.url, body)            # 或 sched.failure(task.url, exc)
sched.set_cursor("a.com", "2026-11-01")  # 断点游标，重启后 get_cursor 读回
```

## 配置

- **归一化**（`NormalizeConfig`，传给 `Scheduler(normalize=...)`）：默认丢弃
  `utm_*`/`gclid`/`fbclid` 等跟踪参数（`drop_params` + `drop_prefixes`，
  可用 `keep_params` 切白名单），query 按键值排序，统一百分号编码，
  小写 scheme/host，去默认端口和 fragment。指纹 = 归一化结果的 SHA-256。
- **站点隔离**（`SitePolicy`）：每个站点独立的 `max_concurrency`（并发上限）
  和 `min_interval`（两次派发最小间隔），未列出的站点走 `default_policy`。
  慢站点占满自己的额度后，`next()` 自动跳过它派其他站点的任务。
- **错误分类**（`crawlflow.errors`）：`NetworkError`（及 `OSError`、5xx/429）
  指数退避重试；`ParseError` 把样本记入 `parse_samples` 表后跳过；
  `PermanentError`（及 4xx）直接落死信。`failure(url)` 不传 error 走兼容
  路径：立即重试，3 次后落死信。

## 状态机

```
pending ──next()──▶ running ──success()──▶ done
  ▲                  │
  │                  ├─failure(network)─▶ pending   (next_run_at = now + 退避)
  │                  ├─failure(parse)───▶ skipped   (样本入 parse_samples)
  │                  ├─failure(permanent)▶ dead     (死信，dead_letters() 可查)
  │                  └─退避次数用尽──────▶ dead
  └────── 重启恢复：所有 running 重置为 pending
```

`tasks` 表持久化状态、重试次数、下次执行时间、指纹和站点；`cursors`
表存断点游标；`results` 表存结果。进程重启后用同一 `db_path` 构造
`Scheduler` 即接着跑，已完成和死信任务不会因变体 URL 重复入队。
