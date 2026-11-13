# crawlquota

采集进程资源配额模块（纯 Python 标准库）。运行时可调上限，超额排队
等待而不是直接失败，按任务记账并输出各任务占用与等待时长。

## 配额字段

| 字段 | 含义 | 默认上限 |
| --- | --- | --- |
| `thread` | 线程数 | 32 |
| `fd` | 文件句柄数 | 256 |
| `memory_mb` | 内存估算（MB） | 2048 |

默认等待超时 `WAIT_TIMEOUT = 1.0` 秒，可用 `set_wait_timeout(seconds)`
在运行时调整，`None` 表示默认无限等待。

## 接口

- `acquire(task, kind, amount=1, timeout=None)`：占用额度，返回占用后的
  全局用量；额度不足时排队等待。
- `release(task, kind, amount=1)`：释放额度并唤醒等待者，返回释放后的
  全局用量。
- `release_all(task)`：任务结束时释放其持有的全部额度，返回释放明细。
- `set_cap(kind, value)`：运行时调整某类上限，唤醒等待者重新检查。
- `usage()` / `task_usage(task)` / `wait_stats(task)` / `stats()`：全局
  占用、任务占用、等待统计（`wait_seconds`/`wait_count`）、汇总快照。
- `reset()`：清空全部记账并恢复默认上限与超时（测试/演示用）。

## 等待策略

- 运行中额度不足时在条件变量上排队，有释放或调大上限时被唤醒重试。
- `timeout` 为单次最长等待秒数；`None` 用默认 `WAIT_TIMEOUT`，
  `float("inf")` 表示无限等待，`0` 表示只试一次。
- 超时抛 `QuotaTimeoutError`（`RuntimeError` 子类），等待时长仍计入
  `wait_stats`。
- 申请量超过上限本身永远无法满足，立即抛 `QuotaError`，不排队。
- 超量释放按当前占用截断（兜底清理/测试复位），不抛错。

## 错误路径

- 未知 `kind` → `KeyError`；非法 `amount`/`timeout`（非数值、负数、
  零）→ `ValueError`。
- 申请超过上限 → `QuotaError`；等待超时 → `QuotaTimeoutError`。
- 所有异常都带明确消息（任务、类型、数量、当前占用），不静默跳过。

## 运行

```bash
python3 demo.py                          # 多任务抢额度，输出占用与等待时长
python3 -m unittest discover -s tests    # 单任务串行跑测试
```
