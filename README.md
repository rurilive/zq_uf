# filelineage

文件级血缘与影响分析，纯 Python 标准库。对外接口保持 `record(job)` 和
`impact(path)`，按任务读写建立文件之间的派生边，支持多跳、环路截断、断点定位、
改名合并与整图导出。

```bash
python3 demo.py                          # 两跳文件链 raw.csv -> clean.csv -> report.csv
python3 -m unittest discover -s tests    # 文件边、多跳、环路、查询
```

## 节点口径

- 节点是**文件路径**，登记前统一分隔符（`\` 视作 `/`）并做 `posixpath.normpath`
  规范化，`./data/x` 与 `data/x`、`data\out.csv` 与 `data/out.csv` 是同一个节点。
- 目录不是节点；目录只作为查询过滤条件（`under="stage"`）。
- 文件改名不产生新节点：改名声明把旧路径折叠到新路径，`export_graph()` 在节点上
  用 `aliases` 保留旧名，图中不会同时出现新旧两个节点。

## 边口径

- 边是 `source -> target` 的**派生关系**：同一次 `record` 里，每个“读到的文件”与
  每个“写出的文件”两两成边。没有读或没有写的任务不产生边。
- 边按 `(source, target, batch)` 归集；同一对文件在同一批次里被多次触达时合并成
  一条边并累加 `count`。
- 边记录 `read_modes`、`write_modes`、`batch`、`jobs`（贡献该边的任务 id），因此
  任务级血缘可以按文件聚合查看（`job_lineage(job_id)`）。
- 写成 `{"path": ..., "mode": "rename"}` 或走 `renames` 声明的改名会被折叠成同一
  节点，改名本身不产生自环派生边。

## 接口

- `record(job)`：记录任务并重建文件级图。`job` 字段为 `id`、`reads`、`writes`，
  可选 `batch`、`renames`、`edges`。读写条目可写路径字符串或
  `{"path", "mode", "batch"}`。返回该任务的读/写数量、贡献边数和 `path`。
- `impact(path, direction="both", max_depth=None, batch=None, under=None)`：
  返回 `depth`（最大层级）、`nodes`（按路径排序，含 `depth`/`direction`/`layers`）、
  `downstream`/`upstream`、`paths`（下游链）、`upstream_paths`、`breakpoints`、
  `cycles`、`truncated`。
  - 多跳 BFS 去重，同一文件只保留最短层级；
  - 遇到环时截断回边，`cycles[].path` 给出闭合链，入口节点保留并标 `cycle=True`；
  - 向上追溯找不到生产任务时，`breakpoints` 给出断点文件与层级；
  - `max_depth` 限制跳数，超出记 `truncated=True`；
  - `batch` 可传单个批次或列表，只沿这些批次的边走；
  - `under` 只保留该目录下的结果节点与路径。
- `upstream(path, **kw)` / `downstream(path, **kw)`：单一方向的 `impact`。
- `lineage_path(source, target)`：返回两文件间一条下游路径；断开时
  `connected=False` 并给出可达末端作为断点。
- `job_lineage(job_id)`：把单任务血缘展开成文件级读写与边视图。
- `export_graph(path=None)`：导出 JSON 可序列化的 `{nodes, edges, aliases, batches, jobs}`。
- `reset()`：清空图，供测试隔离。

## 演示

`demo.py` 用两个任务串出两跳文件链，打印 `raw.csv` 的层级影响面、`report.csv` 的
上游、两文件间的完整路径和导出的血缘图。
