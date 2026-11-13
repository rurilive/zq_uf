# csvrepair

按schema修复脏CSV：列数不齐、引号未闭合、字段内嵌换行。纯Python标准库，入口为 `repair(path, schema)`。

## 修复规则
- 引号配对：按CSV规则解析；列数等于schema记 `ok`，跨物理行拼合记 `joined`。
- 列数偏少：尾部补空串，动作 `padded`。
- 引号未闭合：单行按分隔符重切列数刚好等于schema时，优先按单行处理，动作 `resplit`，不再与下一行拼合。
- 列数超宽，或引号未闭合且重切不上：无法确定，整行原样保留，动作 `kept`。
- 异常输入（文件不存在、schema为空、参数非法、写出失败）抛出 `RepairError`，不静默跳过。

## 报告字段
- 报告写在 `<path>.report.json`，修复结果写在 `<path>.repaired`。
- `records[]`：`line` 起始行号、`raw` 原始行、`action` 修复动作、`row` 修复后字段（kept为null）、`note` 说明。
- 汇总：`total` 记录数、`actions` 各动作计数、`checksum` 输出文件SHA256。
- 返回值：`rows`（兼容旧行为）、`repaired`、`actions`、`count`、`out`、`report_path`、`checksum`。

## 参数和阈值
- `delimiter=","`：列分隔符，单字符。
- `quotechar='"'`：引号字符，单字符，且不能与delimiter相同。
- `max_record_lines=10`：单条记录最多跨越的物理行数，超过则按物理行拆开逐行判定，防止未闭合引号吞掉整个文件。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

