# rowlineage

现状：按任务记录输入输出表，主键映射丢弃，查询只到表级。

问题：不知道某行来自哪些输入行，更新传播不到下游，有环时无限展开。

目标：主键级边，增量幂等传播，环路截断，输出路径和层级。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 节点与边

- 节点：`(table, key)`，key 为主键值（str/int/float）。
- 边：`{"src": (table,key), "dst": (table,key), "job": 任务名, "ts": 时间戳}`。
- 边来源：`rows` 显式四元组，或 `sql` 中 `INSERT INTO t (c) SELECT s.c FROM s WHERE s.c = v / IN (...)` 解析出的主键映射 + 过滤值。
- `keys` 只记列映射（`"源表.列" -> "目标表.列"`），不产生行级边。

## 接口

- `record(job)`：记录任务（`inputs/outputs/keys/sql/rows/ts`），返回任务序号；非法输入抛 `LineageError`。
- `trace(table)`：表级，返回 `[{"job","inputs","outputs"}]`；`trace(table, key)` 行级，返回 `upstream/downstream`（带 level）、`paths`、`truncated`；该 key 无边时退化表级。
- `propagate(table, key, update_id=...)`：沿边向下游传播，visited 集合截断环路；同一 `update_id` 重复调用幂等（`updated` 为空）。
- 查询过滤：`tables=` 限定两端表集合，`start=/end=` 限定边时间范围。
