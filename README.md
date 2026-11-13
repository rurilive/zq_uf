# shardwrite

采集结果按日期和序号分片落盘，纯 Python 标准库实现。每条记录是一行
JSON（JSONL），每个分片配一份清单，支持原子发布、校验和校验与按日期
范围读取。

## 目录结构

`write(records, path)` 的 `path` 是存储根目录，按记录的 `date` 字段分目
录，目录内按序号滚动分片：

```
path/
  2026-03-01/
    2026-03-01-000001.jsonl            # 数据分片
    2026-03-01-000001.manifest.json    # 对应清单
    2026-03-01-000002.jsonl
    2026-03-01-000002.manifest.json
  2026-03-02/
    ...
  unknown/                              # 没有 date 字段的记录
```

当前分片行数达到 `max_rows`（默认 10000）或字节数达到 `max_bytes`
（默认 16 MiB）时滚动到下一个序号；阈值传 0 表示不限制。

## 清单格式

每个 `<分片>.manifest.json` 记录该片的元数据：

```json
{
  "shard": "2026-03-01-000001.jsonl",
  "date": "2026-03-01",
  "seq": 1,
  "rows": 10000,
  "bytes": 238140,
  "checksum": "<分片文件完整内容的 SHA-256 十六进制摘要>",
  "time_start": "2026-03-01T00:00:00",
  "time_end": "2026-03-01T02:46:39"
}
```

`time_start`/`time_end` 取记录中 `timestamp`、`time`、`date` 字段的最小/
最大值；没有可用时间字段时为 `null`。`unknown` 分片的 `date` 为 `null`。

## 写入与崩溃语义

分片和清单都先写到同目录的 `*.tmp` 临时文件，`flush` + `fsync` 后用
`os.replace` 原子改名。因此：

- 已发布的分片永远是完整行，崩溃只会留下 `*.tmp` 残留，不会有半行；
- 下次写入自动清理同日期目录下的 `*.tmp` 残留；
- 重跑同一天的数据会并入当前分片或滚动新片，清单持续可追溯，不会出现
  无归属的新旧混写。

## 接口

```python
from shardwrite import read, write

write(records, path, max_rows=10000, max_bytes=16 * 1024 * 1024)  # 返回写入行数

read(path)                                  # 读全部
read(path, date="2026-03-01")               # 精确某一天
read(path, start="2026-03-01", end="2026-03-03")  # 闭区间范围
read(path, start="2026-03-02")              # 单边范围
read(path, verify=False)                    # 跳过 SHA-256 校验
```

读取时先扫清单、按日期范围定位需要的分片，再逐片解析；默认重算
SHA-256，与清单不一致会抛出 `ValueError`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
