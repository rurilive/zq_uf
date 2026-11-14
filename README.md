# cdcordering

现状：按到达顺序覆盖，版本、墓碑和重复投递都不处理。

问题：旧版本覆盖新版本，删除被复活，重复行和冲突无记录。

目标：版本向量收敛，墓碑，幂等，批量原子和冲突记录。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 版本口径

- 事件可带 `version`（版本向量，str->非负int）和 `source`；每行存版本向量和 `deleted` 墓碑。
- 向量逐分量比较：严格更大才覆盖；更小（stale）或相等（duplicate）直接丢弃并记入 `discarded`。
- 墓碑与普通行一样按版本合并，旧版本更新无法复活已删除行。
- 缺 `version` 的事件退化为按到达顺序覆盖（兼容旧行为）。
- 批量应用先整体校验，任一事件非法抛 `EventError`，状态不变（原子）。
- `apply(events, dry_run=True)` 只预览将变化的行（`changes`），不改状态。

## 冲突口径

- 两侧版本向量互不可比（并发），或同版本不同内容，判为冲突。
- 冲突时保留已收敛的一侧，incoming 事件记入 `discarded`（reason=conflict）。
- 冲突记录同时保留两侧的值、版本向量和来源，见 `apply` 返回值和 `conflicts()`。
- `apply` 返回 `{"applied", "changes", "conflicts", "discarded"}`；`snapshot()` 只含收敛结果。
