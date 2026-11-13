# auditlog

数据同步的审计回放模块，纯 Python 标准库。批次级 JSONL 按天切割
（`audit-YYYY-MM-DD.jsonl`），批次号 `b-时间戳-uuid` 全局唯一，配
`index.json` 索引（损坏或缺失时全目录扫描自愈），跨文件可按批次号检索。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

- `record(batch, path=None, *, handler=None, directory=None)`：传 `path` 为
  遗留计数模式（原样追加 JSON 行）；不传 `path` 为批次模式，返回批次号。
  主键字段默认 `id`（可用 `batch["key"]` 覆盖），结果由 `results` 映射或
  `handler(row)` 回调判定，回调抛异常或返回假值记为该主键失败。
- `replay(target, handler=None, *, directory=None)`：`target` 是文件路径时为
  遗留累加模式；是批次号时只重放该批失败主键，回写批次并返回
  `{batch_id, fixed, failed, retried}`，即修复与仍失败两类主键。
- `find_batch(batch_id, directory=None)`：跨日志文件检索批次。
  日志目录默认 `./auditlog_data`，可用 `directory=`、`configure()` 或环境
  变量 `AUDITLOG_DIR` 指定。

## 批次记录字段

`batch_id` 批次号；`created_at` UTC 时间；`key_field` 主键字段；`fingerprint`
输入记录规范化后的 sha256 指纹；`total` 输入条数；`output` 成功条数；
`failed_keys` 失败主键列表；`elapsed` 耗时秒；`records` 原始输入；
`entries` 主键级状态（`key`/`status`/`error`）；`replays` 历次回放结果。

## 回放口径

回放只补 `failed_keys` 中的主键，已成功的记录不再重跑；每次回放把仍失败与
新修复的主键对比返回，并追加到 `replays` 历史、同步更新主键状态与
`failed_keys`，可多次回放直至全部修复。
