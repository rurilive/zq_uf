# lineageimpact

数据血缘影响面查询：多跳 + 字段级 + 环路截断 + 结果缓存。纯 Python 标准库。

```bash
python3 demo.py                     # 三跳链路 / 字段级 / 环路 / 命中率
python3 -m unittest discover -s tests
```

## 节点口径

| 节点 | 表示 | 示例 |
| --- | --- | --- |
| 表节点 | 表名字符串 | `"raw"` |
| 字段节点 | `(表名, 字段名)` 二元组 | `("raw", "id")` |

字段节点在输出里统一渲染成 `raw.id` 形式。

## 边口径

```python
record("raw", "dwd")                              # 表级边
record("raw", "dwd", "id", "user_id")             # 字段级边（同时补表级边）
record_field("raw", "id", "dwd", "user_id")       # 同上，显式写法
```

* 表级边与字段级边分两份存储，互不混淆。
* 写入字段级边时会自动补齐对应表级边，所以表级查询也能看到字段级新增的下游。
* 任何一次 `record` / `record_field` / `invalidate` 都会让已有缓存整体失效。

## 接口

```python
impact(table, field=None)   # 影响面，返回 ImpactResult（list 子类）
paths(node)                 # 到 node 的命中路径，返回 PathResult（list 子类）
```

两者返回值仍是 `list`，可直接和普通列表比较；明细挂在属性上：

| 属性 | 含义 |
| --- | --- |
| `result[i]` | 下游表名（按影响程度排序） |
| `details` | `[{table, level, paths}]`，表级层级与命中路径 |
| `fields` | `[{table, field, level, paths}]`，字段级层级与命中路径 |
| `cycles` | 检测到并已截断的环路，形如 `a -> b -> c -> a (cycle)` |
| `clipped` | 是否因路径数/深度上限而截断 |
| `cache_hit` | 本次是否命中缓存 |
| `field_fallback` | 字段查询无字段级出边、退回表级时为 True |
| `as_dict()` | 上述内容的 dict 形式 |

`paths(node)` 接受表名、`(table, field)` 元组或 `"table.field"` 字符串。表节点作根且没有上游时返回 `["raw"]`，保持原有口径。

### 排序与层级

* `level` 是从起点出发的**最短跳数**（BFS），起点为 0，不会出现在自己的下游里。
* 结果先按 `level` 升序，再按表名字典序；同表多路径时表只出现一次，`paths` 汇总全部命中路径。
* 路径按跳数由近到远、再按字典序排列。

### 环路

当前 dfs 路径上再次出现某个节点即判为环路：路径记成 `... -> 起点 (cycle)` 并截断，不继续深入；该节点在其它非环路径上仍会正常输出。菱形（两条路径汇到同一节点）不算环。

另有安全上限（`_MAX_DEPTH=32`、`_MAX_PATHS_PER_NODE=16`、`_MAX_EXPANSIONS=20000`）防止 DAG 路径爆炸，触顶时 `clipped=True`。

### 缓存口径

* key = `("impact", table, field)` 或 `("paths", node)`；同名重复查询直接命中。
* 命中时返回副本（`cache_hit=True`），调用方修改返回值不会污染缓存。
* 血缘变更后整体失效：`record` / `record_field` / `invalidate` / `clear_cache` 都会清空缓存并递增 `_version`，下一次查询重新计算且 `cache_hit=False`。
* 邻接表（含反向表）按 `_version` 缓存，避免重复建图。

### 命中率输出

```python
from lineageimpact import cache_stats, cache_hit_rate

cache_stats()
# {'hits': 2, 'misses': 3, 'total': 5, 'hit_rate': 0.4,
#  'by_kind': {'impact': {'hits': 2, 'misses': 3, 'hit_rate': 0.4},
#              'paths':  {'hits': 0, 'misses': 0, 'hit_rate': 0.0}},
#  'size': 1, 'version': 4}
```

`demo.py` 末尾会单独打印命中率（分 `impact` / `paths` 与总计）。`reset()` 清空图、缓存和统计。

## 兼容性

原有接口与断言不变：`impact("raw") == ["dwd"]`、`impact("raw", "id") == impact("raw")`（无字段级边时回退表级）、`paths("raw") == ["raw"]`。
