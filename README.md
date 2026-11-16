# irplaybook

安全事件处置编排小工具：按事件类型登记处置步骤与前置条件，执行时留痕，
关闭前做门禁检查，时间线直接作为复盘材料。

## 编排模型

每类事件（`ransomware` / `phishing` / `intrusion`）在模板中登记有序步骤，
每个步骤包含：

| 字段 | 含义 |
| --- | --- |
| `pre` | 前置步骤，全部完成后本步骤才可执行；清理类步骤把取证步骤列为前置，防止证据被破坏 |
| `required` | 必做项，关闭前必须 `done`；可选步骤失败可凭遗留项确认走关闭流程 |
| `forensic` | 取证标记，时间线中显式标出 |
| `group` | 并行组标识，同组步骤在 `parallel_with` 中互相标注，对端失败不阻断本组步骤 |

## API

- `open(event)`：按类型开单，`event` 含 `id` / `type` / 可选 `operator`，返回步骤汇总。
- `run(event_id, step, operator, result, note="")`：登记一次执行，`result` 为
  `done` / `failed` / `timeout`。前置未满足、步骤已完成或事件已关闭时抛 `ValueError`。
  失败或超时会登记遗留项并停在当前步骤，后续步骤保持 `blocked`，不自动推进；
  重试成功自动核销遗留项并放行下游。
- `confirm_leftover(event_id, step, confirmer, note)`：逐项确认遗留项，记录确认人和时间。
- `close(event_id, operator)`：关闭事件。双门禁——必做项全部 `done`，
  且遗留项全部确认或核销——不满足时抛错并列出缺项；关闭后不可再执行步骤。
- `timeline(event_id)`：返回步骤状态（含 `runnable` / `blocked` / `parallel_with`）、
  按时间排序的操作记录（含操作人、时间、结果，不同操作人逐条标注）、遗留项和
  `close_check` 逐项结果、`closable` 结论。
- `stats(kind=None)`：对**已关闭**事件按类型统计耗时（总数、合计/平均/最小/最大秒数）。
- `set_clock(func)` / `reset()`：注入测试时钟、清空已开单事件。

## 关键规则

1. 步骤只能在前置完成后执行，取证步骤因此先于清理步骤。
2. 步骤超时或失败即停，后续步骤不自动执行；并行组内对等步骤互不阻断。
3. 遗留项关闭前必须逐项确认（或重试成功自动核销）；必做项即使确认了遗留项，
   仍须补做完成才能关闭。
4. 处置记录按时间排序（同一时刻以内部序号保序），每条记录操作人独立标注。
5. 只有已关闭事件进入耗时统计。

## 演示与测试

```bash
python3 demo.py
python3 -m unittest discover -s tests -v
```
