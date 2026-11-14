# 改造留档：lineageimpact 多跳 + 字段级

留档日期：2026-11-15

## 背景

原实现只有 `_edges`（表 -> 表）和一跳查询；`field` 参数被忽略，`paths` 恒返回自身，无缓存。

## 改动清单

| 文件 | 改动 |
| --- | --- |
| `lineageimpact/impact.py` | 重写：表级/字段级双图、多跳 BFS+DFS、环路截断、结果与邻接表缓存、命中率统计 |
| `lineageimpact/__init__.py` | 导出 `record`、`record_field`、`invalidate`、`reset`、`cache_stats` 等 |
| `tests/test_impact.py` | 保留原有 3 个断言不动；新增多跳、字段级、环路、缓存 25 个用例 |
| `demo.py` | 三跳链路、字段级、paths、环路、缓存命中率五段演示 |
| `README.md` | 节点口径、边口径、接口、排序层级、环路、缓存与命中率 |

## 关键设计决策

1. **返回值保持 `list` 兼容。** `ImpactResult` / `PathResult` 继承 `list`，所以旧断言 `impact("raw") == ["dwd"]` 等无需修改；层级、路径、环路等明细作为属性附加。
2. **双图分开存储。** 表级边 `_edges`、字段级边 `_field_edges`（key 为 `(table, field)`）。写字段级边时补齐表级边，表级查询不漏。
3. **字段查询的兼容回退。** 某字段没有任何字段级出边时，退回表级下游并置 `field_fallback=True`，这保住了 `impact("raw","id") == impact("raw")` 这条旧断言。有字段级出边后则严格按字段走。
4. **层级 = 最短跳数（BFS）。** 结果按 `(level, table)` 排序，即“影响程度”按跳数由近到远。
5. **环路 = 当前 DFS 路径上重复出现节点。** 记录为 `环入口 -> ... -> 环入口 (cycle)` 并截断；源表永远不出现在自己的下游里（含自环）。菱形汇合不算环。
6. **缓存整体失效。** 任何 `record`/`record_field`/`invalidate`/`clear_cache` 都会 `_version += 1` 并清空结果缓存与邻接缓存；命中返回深拷贝副本，防止调用方篡改污染。
7. **命中率单独输出。** `cache_stats()` 给出总命中率及 `by_kind`（impact / paths）分项；`demo.py` 末尾独立打印一节。

## 验证

```text
python3 -m unittest discover -s tests   -> Ran 28 tests, OK
python3 demo.py                         -> 三跳/字段/环路/命中率均正常输出
```

## 已知边界

* 路径展开有安全上限（深度 32、单节点路径 16、总展开 20000），触顶置 `clipped=True`；这是防 DAG 路径爆炸的兜底，不是业务语义。
* `reset()` 只清内存，不落盘；无持久化需求。
* 字段名含 `.` 时，`paths("table.field")` 的字符串解析会按最后一个点切分，此时请传元组形式。
