# partquery

分区数据查询层，纯 Python 标准库实现。接口保持不变：`query(dataset, filters)`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 数据布局与参数

- 根目录：`ROOT = "warehouse"`，数据集路径为 `warehouse/<dataset>/`。
- 分区文件：JSONL，文件名必须是 `key=value.jsonl`；多字段分区用逗号连接，如 `dt=2026-02-01,city=sh.jsonl`（分隔符 `PARTITION_SEPARATOR=","`）。
- 同一数据集内分区字段必须一致，否则报 `InvalidPartitionError`；隐藏文件（`.` 开头）与子目录跳过。
- 行内空行会跳过；非法 JSON 行报 `PartitionReadError`，不会静默吞掉。

## filters 语法

`filters` 是 dict，key 为字段名，value 支持两种形态：

- 标量：等值条件，如 `{"city": "sh"}`，等价于 `{"city": {"eq": "sh"}}`。
- 范围 dict：算子取值仅限 `eq`、`ne`、`gt`、`gte`、`lt`、`lte`（见 `SUPPORTED_OPS`），如 `{"amount": {"gte": 10, "lt": 100}}`；空 dict 或未知算子报 `InvalidFilterError`。

## 分区裁剪规则

1. 字段名出现在分区文件名中的条件是**分区条件**，在打开任何文件之前完成裁剪；其余条件是**行级条件**，下推到 JSONL 逐行读取时判定，不满足的行不会进入结果。
2. 时间范围（如 `dt`）按分区值字符串字典序比较：ISO `YYYY-MM-DD` 日期的字典序与时间序一致。`gte`/`lte` 为闭区间，`gt`/`lt` 为开区间，多个算子取交集。
3. 等值条件只保留该分区；带分区条件但没有任何分区命中时，抛 `MissingPartitionError`。
4. 不带分区字段的过滤（如只按 `city` 查）无法裁剪，回退为全分区扫描 + 行级下推。
5. 行内缺失被过滤字段时该行不命中；类型不可比较（如字符串与数字比大小）抛 `InvalidFilterError`。

## 错误口径（异常路径不静默）

| 场景 | 行为 |
| --- | --- |
| 带分区条件，但无命中分区 | 抛 `MissingPartitionError`，错误信息附带现有分区列表 |
| 数据集目录不存在，且 filters 含分区字段 | 抛 `MissingPartitionError` |
| 数据集目录不存在，且 filters 不含分区字段（如 `{}`） | 保持历史行为，返回空结果 |
| 分区文件名无法解析 / 分区字段不一致 | 抛 `InvalidPartitionError` |
| 算子未知、范围为空、filters 类型错误、取值不可比较 | 抛 `InvalidFilterError` |
| 命中文件无法读取或含非法 JSON 行 | 抛 `PartitionReadError` |
| 计划执行时分区文件已被删除 | 抛 `MissingPartitionError` |

所有异常继承自 `PartQueryError`，调用方 catch 基类即可统一处理。

## 返回统计

`query` 返回 dict，历史字段 `rows`、`scanned`、`missing` 保持不变，新增：

- `partitions`：实际扫描的分区文件名列表（排序后）。
- `scanned_total`：数据集分区总数，`scanned / scanned_total` 即裁剪比例。
- `row_count`：扫描分区内读取的总行数（过滤前）。
- `elapsed_ms`：文件读取与行级过滤耗时，毫秒，浮点保留 3 位小数。

## 可序列化状态与重启复现

`plan_query(dataset, filters)` 返回裁剪计划，仅由 str/list/dict 等基本类型组成，可直接 `json.dumps` 落盘；进程重启后 `execute_plan(plan)` 按计划中的分区列表原样执行。`query` 内部即 `execute_plan(plan_query(...))`，两次执行只要分区文件不变，`rows` 与 `partitions` 完全一致（`elapsed_ms` 是测量值，每次不同）。计划与返回结果都可 JSON 序列化，不依赖任何进程内可变状态。
