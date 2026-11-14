# crawlmanifest

现状：一个任务一份清单，直接覆盖。

问题：清单和产物不同步，写一半损坏，并发覆盖。

目标：分片清单，原子写，校验修复和差异恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 清单格式

任务目录下清单与产物分目录存放：`manifests/manifest-shard-NNNN.json` + `data/`。
每份清单记录一个分片：`shard` 分片号、`rows` 行数、`checksum` 产物 SHA-256、
`status`（done/pending/failed）、`artifact` 产物相对路径；行数和校验和可自动计算。
写入先落同目录临时文件再 `os.replace` 原子改名，崩溃不留半截文件；
不同分片不同文件，并发写互不影响。

## 校验与恢复

`verify(path)` 只读不改写，接受清单文件、manifests 目录或任务根目录，
返回 `{ok, missing, extra, corrupt}`：missing 清单有记录但产物缺失，
extra 产物无清单记录，corrupt 清单不可解析或产物校验和不符。
`recover(root, fetch)` 先跑一次 verify，仅对缺失/损坏分片调用
`fetch(shard)` 重新采集并重写对应清单，最后复验；全部正常时不做任何写操作。
