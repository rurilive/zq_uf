# lineage

现状：记录每个任务的输入输出，查询直接消费者。

问题：SQL 临时表识别不到，改名断链，只看一跳。

目标：解析 SQL、消解临时表、别名和多跳展开。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 数据模型
- 节点 `records[job]`：`inputs` 外部输入表、`outputs` 外部输出表、`temps` 任务内消解的临时表（表名统一归一化为小写 `db.table`）。
- 边 `edges`：`job` 产生边的任务、`source` 上游表、`target` 下游表；只存一跳（输入 × 输出）。
- 别名 `aliases`：旧名 → 新名；`rename(old, new)` 或任务配置 `renames` 改名后旧名仍可查询。
- `parse_sql` 识别 `INSERT INTO/OVERWRITE`、`CREATE [TEMP] TABLE/VIEW` 为目标，`FROM/JOIN` 为来源，`WITH` CTE 与 TEMP 表为临时表。
- `impact(table, direction="downstream"|"upstream")` 按图展开多跳，返回任务列表并附 `.levels` 层级。
