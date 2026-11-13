# pipelineview

数据管道依赖视图：任务 / 表 / 字段三级节点，纯标准库。配置加运行记录补全实际依赖，故障时可按任务、表、字段查影响面。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 节点类型

- 任务节点：job 的 `name`，如 `clean`
- 表节点：读写的表名，如 `orders`
- 字段节点：`表.字段`，如 `orders.amount`（故名字里不允许含 `.`，且三类节点名不得冲突）

## 构建与依赖补全

`build(jobs, runs=None, min_runs=1)`：jobs 每项 `{"name", "depends"?, "reads"?, "writes"?}`，reads/writes 条目为表名或 `{"table", "fields"?}`；runs 每项 `{"job", "reads"?, "writes"?}`。
补全规则：B 读了 A 写的表但 `depends` 没声明，则推断边 `A -> B`，计入返回值的 `inferred`。配置声明的读写计 1 次观测，每条运行记录各计 1 次；观测数达到 `min_runs`（默认 1，须 >= 1）才推断。

## 查询口径

- `impact(node, depth=1, paths=False)`：下游。默认 `depth=1` 只返回直接下游（排序列表）；`depth=None` 为全部传递下游。
- `upstream(node, depth=1, paths=False)`：上游，口径相同。
- `paths=True` 时返回 `{"paths": 所有简单路径, "levels": 节点 -> 层级}`，层级即最短跳数；环不会导致死循环。
- 查询按节点名自动识别任务 / 表 / 字段，结果跨类型混合返回（如任务故障可看到受影响的表、字段和报表任务）。

## 导出与恢复

`export(path=None)` 返回可 JSON 序列化的 dict（给 path 则同时写文件）；`load(data)` 恢复视图，重启后查询结果可复现。

## 错误

所有异常输入抛 `ViewError`（ValueError 子类），不静默跳过：空 jobs、任务名重复 / 非法、depends 指向未知任务、运行记录指向未知任务、读写条目非法、节点名冲突、查询未知节点、depth 非法、load 数据缺字段或含悬空边。

