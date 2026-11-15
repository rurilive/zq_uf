# jobrecover

采集任务的阶段级检查点与恢复模块，纯 Python 标准库实现（落盘用 JSON，锁用 `fcntl`）。

## 解决的问题

旧实现检查点只存阶段名且只在内存里：恢复一律从起始阶段重来、重复抓已入库页面；
检查点写一半损坏无法判断；并发采集任务共用一个字典、恢复时互相覆盖。

## 阶段口径

- 默认流水线：`start → fetch → parse → store → finish`，构造 `JobRecovery(base_dir, stages=...)` 可自定义。
- 每个阶段有两种检查点：
  - **完成点** `save(job, stage, state)`（`complete=True`，默认）：标记该阶段完成，进入已完成前缀，恢复时跳过。
  - **位点点** `save(job, stage, state, complete=False)`：只推进当前未完成阶段的抓取位点，阶段仍可继续跑，恢复回到该阶段继续。store 阶段每入库一页写一个位点点，中途崩溃后已入库页面不重复。
- **阶段依赖**：已完成阶段必须是流水线的连续前缀；跳阶段、逆序保存抛 `StageDependencyError`。
- **版本号**：每次 save 生成单调递增的状态版本号；每个阶段的状态各自带保存时的版本。
- **保存失败不推进**：序列化失败或磁盘写失败时指针不更新，`resume` 仍指向上一有效点。
- **重复恢复一致**：`resume` 只读不写，重复调用、换新实例（新进程）读同一目录返回完全相同的结果。
- **阶段耗时**：用 `with recovery.stage(job, name) as ctx:` 包裹阶段逻辑，正常退出自动保存并把 `time.perf_counter()` 耗时写入检查点和 `jobrecover` logger；阶段内抛异常则不写检查点、不推进。

## 回退口径

- 检查点目录按任务划分：`<base_dir>/<url编码的job名>/`，任务之间零共享；job 名整体编码，杜绝路径穿越。
- 目录内每个世代一个 `cp-NNNNNN.json`，另有 `current.json` 指针；写入走"同目录临时文件 + fsync + `os.replace` 原子替换"，写一半只会留下临时文件，旧检查点不受影响。
- 每个检查点含 sha256 校验和（对除 `checksum` 外的全部字段做规范化 JSON 哈希），恢复时校验：格式版本、任务标识、校验和、版本号、阶段依赖前缀、`stage_complete` 衔接关系。
- 当前点（指针声明的文件或指针本身）损坏时，按世代从新到旧扫描，**回退到最近一个有效点**，结果中 `rollback=True`、`rollback_from` 标明损坏点，并打 WARNING 日志；全部损坏则按全新任务从首阶段开始。
- 同任务的 save/resume 通过目录内 `lock` 文件加 `flock` 排他锁串行化，并发采集互不覆盖。每任务只保留最近 5 个世代。

## 接口

```python
from jobrecover import JobRecovery

recovery = JobRecovery("./checkpoints")
recovery.save(job, stage, state, elapsed=None, complete=True)  # 签名与旧接口一致，新增关键字参数
point = recovery.resume(job)
# point: stage(下一阶段，完成全部为 None)、restored、finished、version、position、
#        state、stages(各阶段状态)、completed/skipped、stored_pages、
#        rollback、rollback_from、checksum
```

模块级 `save(job, stage, state)` / `resume(job)` 旧函数保留原内存语义（旧代码与历史测试兼容），新代码请用 `JobRecovery`。

## 运行

```bash
python3 demo.py                       # 中途失败→恢复→损坏回退→任务隔离
python3 -m unittest discover -s tests # 22 个用例，可反复重跑
```
