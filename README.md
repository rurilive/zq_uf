# metricalert

按根因聚合的指标告警抑制模块，只用 Python 标准库。对外接口是 `emit(alert)` 和 `resolve(key)`。

- **聚合键**：`<根因>|<任务组>`，告警带 `dimension` 时追加 `|<维度>`。根因取 `reason`/`root_cause`/`cause`；任务组取 `job_group`，否则由 `job` 去掉实例号后缀得到（`checkout-3` → `checkout`）。
- **时间窗**：按 `ts` 对齐到固定长度窗口（默认 60 秒，`configure(window_seconds=...)` 可调）。同一聚合键在同一窗口内只投递一条聚合通知，后续告警只更新该通知的 `count`/`affected_jobs`/`jobs`/`first_ts`/`last_ts`。
- **恢复**：`resolve(key)` 匹配聚合键、根因、任务组、维度或任务名，输出恢复汇总（影响任务数、告警条数、首末时间），并关闭窗口。
- **静默**：`silence(key, seconds=...)` 或告警自带 `silent_until`/`silent_for`；静默期内只写抑制记录，到期后由下一次 `emit`/`resolve`/`tick` 自动恢复并留 `reason=silence_expired` 记录。
- **记录**：`records()` / `aggregates()` / `suppressions()` / `recoveries()` 输出聚合、抑制、恢复三类记录。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

示例输出：一次上游故障的 300 条告警只投递 1 条聚合通知（影响 300 个任务），恢复时 1 条汇总；静默期内的告警计入抑制记录，到期后自动恢复。

## 设计留档

- 为什么按 `根因|任务组` 而不是按任务：上游故障会让同一任务组的实例成批报错，实例号不是根因；去掉 `-3`/`.12`/`_2` 后缀后聚合粒度才稳定。
- 窗口沿用告警自带 `ts`（缺省用当前时钟），不依赖接收顺序，回放历史数据结果一致。
- 静默期内不滚动窗口，整段静默的告警汇总进同一条恢复通知，避免恢复数对不上。
- `resolve()` 可重复调用且幂等：已关闭的窗口不再产生恢复记录。
- 静默到期结算挂在 `emit`/`resolve`/`tick` 上，不引入后台线程，方便测试和嵌入。
