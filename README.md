# crawljournal

带校验的预写日志：每条记录带长度、序号和 CRC32，追加即 fsync，崩溃后可安全恢复。

## 记录格式

每行一个 JSON 信封：`{"len": N, "seq": N, "checksum": "xxxxxxxx", "payload": {...}}`

- `len`：payload 序列化（sort_keys）后的 UTF-8 字节数
- `seq`：从 1 开始单调递增的序号，由 append 读取文件尾部自动分配
- `checksum`：payload 字节的 CRC32（8 位十六进制）
- append 写入后 flush + fsync，返回前确保落盘；先截掉崩溃留下的半截尾部再写新帧

## 恢复口径

- 从尾部反向扫描：未换行的半截帧、长度或校验失败的记录直接丢弃
- 只保留尾部序号连续的一段；重复序号只保留一条（以后出现的为准）
- recover 返回记录列表（RecoveryResult），附 `dropped` 丢弃条数和 `last_seq` 最后有效序号
- 非本格式内容（如旧版裸 JSON）视为损坏，抛 ValueError 并指明位置

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
