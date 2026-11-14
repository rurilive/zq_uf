# crawljournal

采集日志的崩溃一致性模块：带长度、序号和 CRC32 校验的预写日志（WAL），纯 Python 标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 记录格式

- 每行一条 JSON：`{"len":N,"seq":S,"checksum":"xxxxxxxx","data":{...}}`
- `len`/`checksum`：对 `data` 规范化 JSON（sort_keys、紧凑分隔符）的 UTF-8 字节求字节数和 CRC32
- `seq`：从 1 递增，append 取最后一条有效记录的序号 +1
- append 整行写入后 flush + fsync，崩溃最多留下尾部半行

## 恢复口径

- 从尾部反向扫描：尾部半行、校验失败、长度不符的记录一律丢弃
- 重复序号只保留第一条；只保留最后一段连续序号（seq 逐条 +1）
- recover 自动截断尾部垃圾并 fsync，打印丢弃条数和最后有效序号
- 返回 `Recovery`（list 子类）：有效记录的 `data`，附 `.discarded`/`.last_seq`/`.seqs`
- 尾部数据不像帧残片时抛 `ValueError`，文件不存在抛 `FileNotFoundError`
