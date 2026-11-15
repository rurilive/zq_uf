# lineageimpact2

字段级血缘影响分析：多跳查询带层级与路径，环路截断标注，断链给出断点，结果按影响程度排序、可缓存、可导出、可回放。

```bash
python3 demo.py                      # 核心表 ods.orders 上的完整验证
python3 -m unittest discover -s tests
```

## 用法

```python
import lineageimpact2 as li

li.add_edge("ods.orders.id", "dwd.order_fact.order_id")
li.add_edge("dwd.order_fact.order_id", "dws.trade_agg.order_id")

r = li.impact("ods.orders.id")       # 字段级；li.impact("ods.orders") 为表级
r["impacts"]                          # 按影响程度降序：table/field/level/status/path/impact
r["direct_impacts"], r["indirect_impacts"]
r["paths"], r["path_scores"], r["path_levels"]
r["cycles"], r["truncation"]          # 环与截断原因（cycle / depth / paths / limit）
r["breaks"]                           # 断点：起点缺失，或遇到表级血缘（字段映射未知）

li.export(r, "csv"); li.save(r, "impact.json")
li.replay(g.events())                 # 事件日志回放
```

- 排序：路径得分 = 边权重连乘 × `decay**(跳数-1)`，权重默认 1（可用 `set_edge_weight` 调整）。
- 缓存：同一查询参数命中缓存；任何建边 / 改名 / 改权重 / 删边都会让图版本 +1 并清空缓存。
- 改名：节点内部 id 不变，旧名保留为别名，重复添加不会产生重复节点。
- 环路：路径上出现重复节点即截断并记入 `cycles`，不会陷入死循环。
- 表名允许带点；有歧义时传 `(表, 字段)` 元组。
