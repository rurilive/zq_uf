# reconcheck

纯标准库的表对账：`compare(left, right, config)` 按 `keys`（默认 `["id"]`，可组合键）
全量匹配，输出 `left_only`、`right_only`、`mismatches` 三类差异和 `status`（pass/fail）。

- 精度：`tolerance: {"abs", "rel"}` 为默认数值误差，`field_tolerances` 按字段覆盖；非数值字段严格相等。
- 重复主键：记入 `duplicate_keys` 并判失败，不静默取一条。
- 分区：`partition_by` 按分区字段并行对账（`workers` 控并发），仅单侧存在的分区列入 `missing_partitions`。
- 流式：输入可为迭代器，外排归并、内存有界，`stream_chunk_size` 控块大小。
- 例外：`exceptions` 规则（`keys` / `fields` / `max_abs` / `max_rel`）命中的差异降级进 `warnings`，不影响 `status`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
