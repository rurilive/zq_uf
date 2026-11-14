# partitionmap

规则路由 + 迁移：分区按规则落到存储前缀，扩容只动映射内的分区，不再全量迁移。

## 路由规则

- 规则字段：`name`、`dataset`（精确或 `*`/通配）、`start`/`end`（按分区里的 `dt=` 比较）、`tags`（子集匹配）、`target`（支持 `{dataset}`/`{partition}` 占位）、`priority`。
- 多条命中时 `priority` 高者胜；同优先级按 `name` 字典序，路由结果稳定，不再被后写的覆盖。
- 冲突时返回的 `RouteResult.conflict` 给出命中的前两条规则、胜者和解决依据。
- 无规则命中时回退 `dataset/partition`；`route()` 返回值是 str，路径之外还带 `rule`/`conflict`/`old_path`/`dual_read`。
- 规则表落盘 `<state_dir>/rules.json`。

## 迁移口径

- `migrate({"action": "prepare", "dataset": ..., "partitions": [...], "to": 前缀})`：生成 from→to 映射并进入双读，`route` 走新路径且带 `old_path` 兜底，`moved=0`。
- `migrate({"action": "switch", "id": ...})`：停旧路径，`moved` 为迁移的分区数。
- `migrate({"action": "rollback", "id": ...})`：回滚，`route` 回到旧路径。
- 迁移记录追加落盘 `<state_dir>/migrations.json`，状态机 prepare→switch / rollback。
- state_dir 默认 `.partitionmap/`，可用 `PARTITIONMAP_STATE_DIR` 或 `partitionmap.configure(state_dir=...)` 覆盖。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
