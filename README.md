# crawlledger

幂等采集账本：按批次隔离的追加日志，纯 Python 标准库。

## 账本格式

每批次一个文件 `batch-<批次>.jsonl`，条目行与提交行交替成块：

- 条目行：`{"type":"entry","batch","seq","page",...,"checksum"}`，`checksum` 为条目内容（不含 checksum 字段）的 SHA-256。
- 提交行：`{"type":"commit","seq","checksum"}`，`checksum` 为链式校验和（上一块链值 + 条目行），序号从 1 连续。

## 口径

- **幂等**：键为 `(batch, seq)`。同批次同序号且内容一致的重复写入判重跳过（`duplicate=True`）；同序号内容冲突、序号跳跃抛 `ValueError`；不给 `seq` 时自动连续编号（不判重）。
- **原子写**：进程内锁 + 跨进程锁文件串行化，先写临时文件再 `os.replace` 原子改名；写入失败旧文件不动，即回滚到上一个完整块。
- **校验修复**：`verify(batch)` 检查半行、条目校验和、序号连续性、块链校验和；发现坏块截断到最后一个完整块并原子重写修复。`verify(batch, snapshots)` 额外将条目 `snapshot` 字段与页面快照 `{page: 校验和}` 对账，报告 `mismatched`/`missing`。
- **输出**：`append` 返回 `appended/duplicate/total/repaired/seq`；`verify` 返回 `rows/half/repaired/ok/pages`（`ok` 为修复并对账后的账本一致性）；`ledger_size()` 随时查看目录、字节数、批次数、条目数。
- **恢复**：`reset()` 清空进程内缓存模拟重启；`verify(...)["pages"]` 列出已完成页面。默认账本目录为进程级临时目录，`configure(dir)` 或环境变量 `CRAWLLEDGER_DIR` 可指定持久目录。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
