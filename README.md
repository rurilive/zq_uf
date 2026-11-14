# crawlpause

采集任务的按任务暂停/恢复模块。纯 Python 标准库（`threading` / `json` /
`os` / `tempfile`），无第三方依赖。

## 解决的问题

旧实现只置一个全局内存标志：在跑任务被硬杀、已写结果不回滚、恢复后从头
重抓导致重复入库，且单一信号无法分别控制多个任务。现在每个任务独立维护
阶段机、检查点和幂等存储。

## 阶段口径

| 阶段 | 含义 |
| --- | --- |
| `running` | 正常采集，按批次处理数据项 |
| `pausing` | 已收到暂停：置**收尾态**（不开新批次），等当前批次自然收尾 |
| `paused` | 批次已收尾（或超时中断），检查点已原子落盘 |
| `resuming` | 从检查点恢复的瞬间态，随后重新进入 `running` |
| `failed` | 处理器抛异常，错误带任务 id、数据项下标和幂等键 |

暂停分两级信号：`drain_pending`（收尾态，只禁止开新批次，当前批次仍可
正常写入直到结束）与 `read_only`（只读，超时中断时才升级置位，当前批次
未写的项留给恢复重放）。`pause(task, drain_timeout=5.0)` 先等收尾，超时
才中断当前批次并在返回结果里记录 `interrupted=true` 与 `interrupt_reason`。

## 检查点口径

- 检查点游标 `safe_cursor` 是**已完整收尾的批次数**，只在批次边界推进，
  落在批次开头；批次进行中暂停/中断都不会推进它。
- 落盘为 `checkpoint_dir/checkpoint-<task>.json`，先写临时文件再
  `os.replace` 原子替换，写失败抛 `CheckpointError`。
- 中断时批次内已写入的结果**不回滚**；恢复从该批次开头重放，由
  `IdempotentStore` 按幂等键去重（语义＝至少一次 + 幂等入库），重放被挡
  下的项计入 `duplicates`，不产生重复数据。
- `resume(task)` 会校验检查点归属任务、核对内存游标与文件游标，不一致抛
  `CheckpointError`。

## 用法

```python
from crawlpause import Coordinator, CrawlTask

coord = Coordinator(checkpoint_dir="./checkpoints")
task = CrawlTask(
    "task-A",
    [{"key": "k1", "data": ...}, ...],
    batch_size=10,
    coordinator=coord,
)
task.start()

result = coord.pause("task-A", drain_timeout=5)   # 等收尾，超时则中断
print(result["phase"], result["checkpoint"], result["interrupt_reason"])

result = coord.resume("task-A")                   # 从检查点继续
print(result["resumed_from_cursor"])
```

模块级门面保持旧签名 `pause(task)` / `resume(task)` / `is_paused(task)`，
背后是一个默认 `Coordinator` 单例；测试或多目录场景直接实例化
`Coordinator` 做隔离。

## 幂等与异常

- **幂等**：任务已暂停时重复 `pause` 返回同一结果，阶段不变、检查点不
  重写；任务已在运行时重复 `resume` 返回同一恢复结果，不重启工作线程。
- **异常路径**：恢复未知/未暂停任务抛 `TaskNotPausedError` /
  `TaskStateError`，检查点读写失败抛 `CheckpointError`，处理器失败抛
  `TaskExecutionError`（统一基类 `CrawlPauseError`），消息均带任务 id。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
