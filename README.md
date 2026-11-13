# partitioncatalog

可查询的分区元数据服务，纯 Python 标准库。登记时写元数据，查询读索引文件，
不再遍历目录；分区增删改都写事件日志；启动时 `init()` 扫描目录修复缺失记录。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

- `register(partition)`：登记分区，返回路径。重复登记同一 `(dataset, name)` 时，
  元数据有变化则覆盖并记 `update` 事件，完全一致则不记事件。
- `list_partitions(dataset, start=None, end=None)`：返回排序后的分区名列表；
  给定 `start`/`end` 时按时间范围过滤。
- `get(dataset, name)` / `remove(dataset, name)`：查单条元数据 / 删分区（记录和目录）。
- `init()`：启动修复，补登目录里缺失的记录，返回修复的 `(dataset, name)` 列表。
- `read_events()`：按时间顺序读事件日志。

## 元数据字段

`partition` 为 dict，`dataset`、`name` 必填，其余可选：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `dataset` | str | 数据集名，非空、不能含路径分隔符、不能以 `.` 开头 |
| `name` | str | 分区名，约束同上 |
| `start` / `end` | str | ISO 日期 `YYYY-MM-DD`，必须成对出现且 `start <= end` |
| `rows` | int | 行数，非负整数（`bool` 不算） |
| `checksum` | str | 校验和，非空字符串 |

存储位置：`warehouse/.catalog/partitions.json`（另加 `path`、`updated_at`），
事件日志为 `warehouse/.catalog/events.log`，每行一条 JSON：
`{"ts", "event", "dataset", "name", "detail?"}`，event 取
`register` / `update` / `remove` / `repair`。`update` 的 `detail.changed`
记录变化字段的新旧值。

## 查询口径

- 范围查询为闭区间重叠：分区 `[start, end]` 与查询 `[start, end]` 有交集（含边界）即命中。
- 无时间范围的分区（未登记 `start`/`end`，或 `init()` 修复补登的）不参与范围查询，
  仅出现在无范围条件的 `list_partitions(dataset)` 中。
- 查询的 `start`/`end` 必须成对给出且 `start <= end`。

## 参数与阈值

- `ROOT = "warehouse"`：数据根目录，相对当前工作目录。
- `META_DIR = ".catalog"`：元数据目录，扫描修复时跳过所有 `.` 开头目录。
- 时间精度为「天」（ISO 日期），无其他阈值；行数、校验和只校验类型不校验内容。

## 错误处理

非法输入一律抛异常，不静默跳过：缺字段、空字符串、路径穿越（`..`、`/`、
`.` 开头）、`start > end`、非法日期、负行数、空校验和抛 `ValueError`；
`get`/`remove` 不存在的分区抛 `KeyError`。

