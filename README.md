# timeoutgov

任务超时治理：按任务类型和历史耗时动态超时，超时保存进度与堆栈现场，支持断点续跑。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

`run(task, deadline=None)`，task 字段：

- `name`（必填）、`work`（必填，可调用）、`type`（可选，默认 `default`）
- `progress`（可选 dict，work 就地更新，超时时作为现场保存）
- `checkpoint`（可选，上次超时保存的断点；传入后调用 `work(checkpoint)` 续跑）

配套：`stats()`（超时任务/耗时分布/恢复结果）、`dump_state()`/`load_state()`（JSON 可序列化，重启后复现）、`reset_state()`、`set_clock()`。

## 超时口径

- `deadline` 显式传入时优先；否则按 `type` 的历史耗时动态计算。
- 动态公式：`limit = mean * HEADROOM_FACTOR + pstdev * STDDEV_FACTOR`，夹在 `[MIN_TIMEOUT, MAX_TIMEOUT]`。
- 无历史数据时用 `DEFAULT_TIMEOUT`；只有成功完成的任务才计入历史。
- 判定为事后口径：work 返回后比较注入时钟的耗时，`elapsed > limit` 即超时。
- 超时抛 `TaskTimeoutError`（`TimeoutError` 子类），携带 `limit/elapsed/checkpoint`。

参数阈值：

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `DEFAULT_TIMEOUT` | 30s | 无历史时的默认超时 |
| `MIN_TIMEOUT` / `MAX_TIMEOUT` | 1s / 3600s | 动态超时上下限 |
| `HEADROOM_FACTOR` | 1.5 | 均值余量系数 |
| `STDDEV_FACTOR` | 3.0 | 标准差倍数 |

## 恢复流程

1. 超时瞬间保存现场：`progress` 快照 + 真实堆栈（`stack`/`last_frame`），存入 `_checkpoints[name]`。
2. 从异常 `exc.checkpoint` 或 `dump_state()` 取回断点（可 JSON 落盘，重启后 `load_state()` 恢复）。
3. 重新提交时把断点放进 `checkpoint` 字段，此时 `work` 须接收一个参数，从断点继续而非重跑。
4. 恢复成功后清除该任务的待恢复现场，并记入 `stats()` 的 `recoveries`。

## 错误路径（均显式抛错，不静默跳过）

- task 非 dict / 缺 `name` / `work` 不可调用 → `TypeError` / `ValueError`
- `deadline` 非正数或非数值 → `ValueError` / `TypeError`
- `progress` 非 dict、`checkpoint` 不可 JSON 序列化 → `TypeError` / `ValueError`
- 传了 `checkpoint` 但 `work` 不接收参数 → `ValueError`
- `load_state()` 收到未知键或类型不符 → `ValueError` / `TypeError`
