# partclone

口径：先取一致性快照（记录版本与行数，克隆期间源分区只读）再复制；
数据写暂存区，校验通过并提交后目标分区才对查询可见。
失败自动清理半成品目标，断点落盘 `checkpoints/`，重试从断点续传，
次数上限 `max_retries`（默认 3）。复制后对比行数与 SHA-256 校验和，
不一致即中止并保留两侧计数；校验结果写审计 `audit.log`。
状态目录默认 `<tmp>/partclone-state`，可用 `PARTCLONE_STATE` 覆盖。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
