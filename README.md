# jobwindow

任务窗口与依赖模块，纯 Python 标准库。接口只有两扇门：`open(window)` 和
`close(window)`，其余是辅助函数（上游登记、任务完成、状态查询）。

```bash
python3 demo.py                          # 演示上游延迟/补跑/并发/失败告警/关闭清理
python3 -m unittest discover -s tests    # 20 个用例
```

## 改造前的问题

- 到点就开窗，上游没完成也开跑；
- 错过窗口直接不跑；
- 并发窗口互相挤占，没有资源组和上限；
- 关闭窗口不清理未完成任务。

## 窗口字典

```python
open({
    "name": "hourly",                  # 窗口名（必填）
    "upstream": ["extract"],           # 上游，字符串 / 字典 / 列表（支持多上游）
    "dependency_policy": "wait",       # wait（默认，等上游）或 skip
    "wait_timeout": 2.0,               # 等待上限秒数（上游和并发槽位共用）
    "scheduled_at": 1710489600,        # 计划开始时间，也接受 window_start/start
    "window_seconds": 300,             # 窗口时长，也接受 duration
    "window_end": 1710489900,          # 或直接给结束时间（deadline/end 亦可）
    "catch_up_policy": "once",         # once（默认，只补一次）或 never
    "resource_group": "etl",           # 资源组，也接受 group，默认 "default"
    "max_concurrency": 2,              # 资源组内并发上限，也接受 concurrency_limit
    "on_concurrency_limit": "skip",    # skip（默认）或 wait
    "tasks": ["extract", "load"],      # 窗口内任务，close 时清理未完成项
    "now": 1710489700,                 # 仅测试用：覆盖当前时间
})
```

上游项可以写成 `"job-a"`，也可以写成
`{"name": "job-a", "check": callable}`，其中 `check` 返回布尔、状态字符串或
`{"status": ..., "detail": ...}`；此外支持在字典里直接写 `done`/`completed`/
`status`。未在 `mark_job` 登记、也没有内联状态的上游按**已完成**处理，
以保持旧调用不被阻塞。

## 窗口与补跑口径

**开窗顺序**：判断是否错过窗口 → 校验上游 → 抢资源组并发槽位 → 开窗。

1. **上游校验**：多上游必须全部完成才开窗。
   - `dependency_policy="wait"`：轮询等待，超过 `wait_timeout` 仍未完成则
     跳过，`reason="upstream_timeout"` 并产出 `alerts`。
   - `dependency_policy="skip"`：不等待，直接跳过，`reason="upstream_pending"`。
   - 任一上游失败（`fail_job` 登记或 `check` 抛异常/返回失败）：下游**不启动**，
     `reason="upstream_failed"`，`alerts` 给出失败上游与原因。失败不会被等待，
     上游恢复（`mark_job(name, "completed")`）后可再次开窗。
2. **错过补跑**：当前时间晚于 `window_end`（或 `scheduled_at + window_seconds`）
   即视为错过。默认策略 `once` 会在开窗时**补跑一次**，结果里
   `catch_up=True`，`catch_up_reason` 记录触发原因（哪个窗口、何时结束、当前
   何时）。同一窗口的**同一场次**（相同的 `scheduled_at`/`window_end`）只会补跑
   一次：已补过后再次触发返回 `opened=False, reason="catch_up_exhausted"`；
   下一个场次（时间不同）可以各自补一次。`catch_up_policy="never"` 时错过即
   跳过，`reason="missed_window"`。
3. **并发上限**：按 `resource_group` 隔离计数，组内已开窗口数达到上限时：默认
   `skip` 跳过，`reason="concurrency_limit"`；`wait` 则等待槽位，超时返回
   `reason="concurrency_timeout"`。窗口级 `max_concurrency` 与组级
   `set_group_limit(group, n)` 同时生效时取更严格者。被跳过或未开成的窗口
   **不占槽位**，`close` 释放槽位。没有配置上限的窗口不受限。槽位抢占与补跑
   去重在同一把锁内完成，多线程同时 `open` 不会超发。
4. **关闭清理**：`close(window)` 把窗口内所有未完成任务标记为 `aborted` 并列入
   `aborted`/`cleaned_tasks`，同时释放资源组槽位。
5. **状态查询**：`status(window)` 返回 `status`（open/closed/unknown）、
   `concurrency`、`group`、`catch_up`、`catch_up_reason`、`tasks`、`aborted`；
   `snapshot()` 返回全局窗口/资源组/已补跑快照。

## 返回字段

`open` 返回 `window, opened, waited, wait_seconds, catch_up, catch_up_reason,
concurrency, group, skipped, reason, alerts, pending_upstream, failed_upstream`。
开窗失败时 `opened=False` 且 `reason` 说明原因：`upstream_pending`、
`upstream_timeout`、`upstream_failed`、`missed_window`、`catch_up_exhausted`、
`concurrency_limit`、`concurrency_timeout`。

## 辅助接口

- `mark_job(name, status)` / `fail_job(name, detail=None)`：登记上游状态；
- `complete_task(window, task)`：标记窗口内任务完成，关闭时不清理；
- `status(window)` / `snapshot()`：查询状态（`snapshot` 含资源组占用与上限）；
- `set_group_limit(group, n)`：设置资源组全局并发上限，`None` 取消；
- `configure(clock=..., sleep=...)`：注入假时钟，测试不依赖真实等待；
- `reset()`：清空运行期状态（测试与 demo 用）。

## 测试口径

`tests/test_window.py` 覆盖依赖校验（等待/等待超时/跳过/多上游/上游失败告警）、
错过补跑（只补一次、场次隔离、never、被上游挡住不消耗补跑机会）、并发上限
（跳过、等待超时、资源组隔离、组级上限、被跳过不占槽位）、关闭清理与状态查询。
原有 3 个断言语义不变：上游未登记时照常开窗、正常窗口不带补跑、无任务窗口
关闭时 `aborted` 为空。
