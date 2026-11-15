# partclone

分区克隆：先取一致性快照再复制，失败清理并从断点续传，完成后校验。纯 Python 标准库。

## 快照口径

- 克隆开始先在源分区上取**一致性快照**：记录 `version`（源分区版本号）与 `rows`（行数），并保存行数据的深拷贝。快照落盘为 `snapshot.json`（含 `checksum`、`taken_at`、`read_only: true`），之后的源分区写入不再影响这份快照。
- 克隆只读源分区：读快照、不写源数据；克隆期间的源写入会同时改变源，但不会进目标，所以目标与快照一致。
- 目标在克隆完成前**对查询不可见**（`query(tag)` 返回 `None`），校验通过后才可见。

## 校验口径

- 对比**行数**与 **SHA-256 校验和**：按行序对每行的规范 JSON（`sort_keys` + 紧凑分隔符）逐行摘要。
- 两侧都保留计数：`rows_source`/`rows_target`、`checksum_source`/`checksum_target`；`reason` 取 `rows` 或 `checksum`。
- 不一致时**中止**克隆、目标不可见，并抛出 `VerifyError`（异常中带两侧计数）；断点相位记为 `verify_failed`。
- 每次克隆与 `verify(tag)` 的校验结果都写入审计 `audit.jsonl`（`attempt`/`resume`/`cleanup`/`retry`/`success`/`verify_failed`/`verify`/`give_up`）。

## 断点与重试口径

- 每复制 `CHECKPOINT_INTERVAL = 16` 行（以及最后一行的偏移）就把断点（已复制 `offset`、快照版本、行数）原子写入 `checkpoint.json`；失败时清理目标半成品（保持不可见），断点保留。
- 重试从断点续传，不会重新取快照，也不需要重新复制已复制的前缀；重试次数上限 `MAX_RETRIES = 3`（可用 `max_retries=` 覆盖），耗尽抛 `CloneError`。
- 进程退出后再次调用 `clone` 会读回落盘快照和断点继续；快照文件完整性（行数/校验和）不符时放弃续传、重新取快照。
- 状态目录默认 `.partclone/<tag>/`，可用 `state_dir=` 或环境变量 `PARTCLONE_STATE_DIR` 覆盖；`fresh=True` 强制重新取快照。

## 接口

```python
from partclone import Partition, clone, verify, query

src = Partition("orders", rows=[{"id": 1, "v": 10}, {"id": 2, "v": 20}])
result = clone(src)                 # 快照 -> 复制 -> 校验
result["snapshot"]                  # {version, rows, checksum, read_only, ...}
result["checkpoint"]                # {offset, phase, retries, snapshot_version}
result["verify"]                    # {ok, rows_source, rows_target, checksum_source, checksum_target}
query("orders")                     # 克隆完成后可见的目标行
report = verify("orders")           # 行数 + 校验和，matched/ok
```

旧的 `clone({"tag": ..., "rows": <int>})` 只按行数复制，`snapshot` 仍为 `None`，`verify` 的 `matched` 仍为 `True`，兼容原有行为。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
