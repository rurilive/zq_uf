# taskresume

采集任务断点恢复的一致性模块，纯 Python 标准库。检查点记录**入库位点**与
**校验和**，先落盘再确认；损坏时回退到上一有效点；按采集任务隔离；恢复后
跳过已入库页面。

## 启用方式

接口签名保持 `save(task, state)` / `resume(task)` 不变。默认是纯内存模式
（不落盘，与历史行为一致）；配置检查点目录后启用一致性落盘：

```python
from taskresume import configure, save, resume

configure("./checkpoints", keep_versions=3)   # 或环境变量 TASKRESUME_CHECKPOINT_DIR
save("job-a", {"page": 5})                    # 页面入库后保存位点
out = resume("job-a")                         # 恢复
# out["start"] == 6        下一个待采集页
# out["position"] == 5     已确认入库的位点
# out["skipped"] == [1..5] 已入库、恢复时跳过的页
```

`save` 的 state 支持三种位点写法，按优先级取：显式 `position` >
`stored_pages` 的最大页号 > `page`；`stored_pages` 用于非连续入库的精确跳过。

## 检查点口径

- **内容**：`format / task / version / position / stored_pages / state / checksum`。
  `checksum` 是对除自身外全部字段做规范化 JSON 后取 SHA-256（64 位十六进制），
  任何篡改或截断都会在校验时暴露。
- **先落盘再确认**：`save` 先写任务目录下的临时文件 `checkpoint-*.tmp`，
  `flush + fsync` 后用 `os.replace` **原子改名**为正式版本，再 fsync 目录；
  全部完成才返回 `durable=True` 和校验和。改名前崩溃只留下 `.tmp`，
  不会污染任何有效版本。
- **版本保留**：每个任务保留最近 `keep_versions`（默认 3）个有效版本，
  下一次 `save` 时清理超出的旧版本和遗留临时文件。
- **任务隔离**：每个任务独占一个目录（任务名经 URL 安全转义，含 `/`、中文
  等也安全），并发采集任务各写各的目录，互不覆盖；进程内同任务写操作加锁。

## 回退与恢复口径

- `resume` 从最新版本向旧版本逐个读取并校验：解析失败或校验和不符即跳过，
  取**第一个有效版本**作为恢复点。
- 最新版本损坏、落到旧版本时返回 `rolled_back=True`；所有版本都损坏时没有
  可信任位点，回退到零（`position=0, start=0, skipped=[]`，同样
  `rolled_back=True`）。
- `resume` 是**只读**操作：不写文件、不删文件，因此可重复调用、可随时中断，
  重复恢复返回完全相同的结果（幂等）。临时文件不参与恢复。
- 跳过页 `skipped`：有 `stored_pages` 时按其清单；只有位点时按连续页
  `1..position` 跳过；`start = position + 1`（位点为 0 时 `start=0`）。

## 返回值

`save`：`task / saved / durable / checksum`（内存模式 `durable=False,
checksum=None`），落盘时另含 `position / version / path`。

`resume`：`task / start / position / skipped / rolled_back / recovered /
checksum / version`；内存模式保持旧口径 `start=0, skipped=[], rolled_back=False`。

## 运行

```bash
python3 demo.py                        # 模拟写一半崩溃（临时文件残留 + 版本截断）
python3 -m unittest discover -s tests  # 旧断言不变，新增 16 个一致性测试
```
