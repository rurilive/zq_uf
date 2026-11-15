# crawlrecover

采集崩溃恢复：位点与已完成键落盘，崩溃后从位点续跑，纯 Python 标准库。

## 接口

- `mark(task, page)`：把已完成页面追加进日志，返回 `{"task", "marked", "seq", "batch", "duplicate"}`；同一批次内重复 mark 幂等，不落重复记录。
- `recover(task)`：从上次位点继续校验日志，返回恢复报告；日志未变时重复调用幂等，直接返回上次报告。

恢复报告字段：`position`（恢复位点）、`completed`（已完成键，采集循环据此跳过）、`skipped`（被跳过的坏记录）、`truncated`（截断记录）、`start`/`end`（本次尝试起止偏移）、`attempt`、`batch`、`batch_reset`、`interrupted`。

## 存储（`$CRAWLRECOVER_HOME`，默认 `./.crawlrecover/`）

- `<task>.journal`：追加式日志，每行一条 JSON：`{"seq", "batch", "ts", "page", "checksum"}`。
- `<task>.recover.json`：恢复位点（seq/offset/batch/ts）、已完成键、上次报告；临时文件 + `os.replace` 原子写入。
- `<task>.recoverlog`：每次恢复尝试的起止位置（`begin`/`done`/`interrupted`/`reset`）。

## 位点与校验口径

- **校验和**：`checksum = sha256("{seq}|{batch}|{ts}|{page}")` 的前 16 位十六进制，逐条校验。
- **位点**：最后一条校验通过记录的 `(seq, offset, batch, ts)`；`seq` 从 1 连续递增，写入带时间戳和批次号。
- **批次**：`recover(task, batch=...)` 指定批次与日志批次不符时，判定位点不属于当前批次，归档旧日志为 `<journal>.<batch>.bak` 后从头开始。
- **半行**：文件末尾无换行的残行视为崩溃残留，截断（报告 `truncated`），截断后后续记录重新逐条校验。
- **坏记录**：JSON 或校验和错误跳过（报告 `skipped`，`bad_json`/`bad_checksum`）；序号回退跳过（`stale_seq`）；序号跳变但校验通过则接受并记 `seq_gap`。
- **中断**：恢复中 `KeyboardInterrupt` 安全落地，进度逐条随位点落盘，再次恢复从同一偏移继续。
- **幂等**：日志未变的重复恢复返回同一份报告；已完成页重复 mark 不再写日志。

## 运行

```bash
python3 demo.py                            # 模拟崩溃：半行截断、位点恢复、中断续跑
python3 -m unittest discover -s tests      # 位点/截断/跳过/幂等/批次/中断用例
```
