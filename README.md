# auditexport

审计日志分片导出：按序号或时间切片，断点续导，逐片校验和，整体校验。纯标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 分片口径
- `export(range, path)`：`range["records"]` 按 `shard_size`（默认 10000 行）序号切片；
  给 `time_key` + `shard_seconds` 则按时间窗切片（记录需按时间有序）。
- 流式逐片写出，不一次性读入内存；分片文件在 `<path>.shards/` 下按序号命名
  （000001.jsonl …），临时文件原子改名。
- 清单 `<path>.manifest.json` 记录每片范围、行数、sha256；全部片写完后拼装 `<path>`，
  清单与返回结果都带总行数和整体校验和（最终文件的 sha256）。

## 断点与校验
- 每写完一片即落清单作断点；中断后 `resume(token)` 从缺口继续，只补缺失/损坏的片，
  完好分片不重写。token 即清单路径，export 返回值和中断异常的 `.token` 都带。
- 已完成的导出重跑先做整体校验：缺片或坏片抛 `ExportIntegrityError`，明确指出片号。
- 幂等：同 range 同 path 重跑直接返回原结果；range 或切片口径变了需换新 path。
- token 无效（不存在、不可解析或无可重读数据源）抛 `NotImplementedError`。
