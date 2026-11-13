# lineage

纯标准库的血缘图：解析任务配置与 SQL，存一跳边，查询按方向多跳展开。

- `record(job)`：job 字段 `name`、`inputs`、`outputs`、`sql`、`renames`；SQL 里的临时表（TEMP 或任务内自产自销）在任务内消解，不进入对外血缘。
- `impact(table, direction="downstream"|"upstream")`：返回受影响任务名列表（按层级顺序），`.levels` 为分层视图。
- `rename(old, new)`：改名后新名为规范名，旧名保留为历史别名，已有边同步改写；表名归一化（去引号、小写、保留 `库.表`）。
- 节点 `nodes[表名]`：`{"name": 规范名, "aliases": [历史别名]}`。
- 边 `edges[i]`：`{"src": 上游表, "dst": 下游表, "job": 任务名}`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
