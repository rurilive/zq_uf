# tablecopy

表复制一致性模块：快照 + 增量，纯 Python 标准库。

## 口径

- 快照：copy 开始取源表一致性快照，记录位点 snapshot_lsn 并落盘。
- 增量：快照期间源表的写入按位点从变更日志补齐（upsert/delete），增量日志落盘 increments.jsonl。
- 校验：复制后对比行数和校验和（每行规范 JSON 的 sha256 之和 mod 2^256，与行序无关）。
- 重试：失败清理目标半成品，最多重试 3 次；位点保留在 .tablecopy/<dst>/checkpoint.json，重试从断点继续，耗尽抛 CopyError。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

状态目录默认 `.tablecopy/`，可用环境变量 `TABLECOPY_STATE_DIR` 覆盖。
