# syncconflict

现状：远端字段直接覆盖本地，没有版本、来源和删除标记。

问题：同时改同一行后到的赢，冲突被静默吞掉，删除和更新交叉会复活。

目标：带版本和来源的冲突解决，支持本地优先、远端优先和人工，删除用墓碑。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

版本规则：
- 每行带 `version`（非负整数）、`updated_at`（时间戳）、`source`（来源标识）；`deleted=True` 为墓碑。
- `version` 高者胜，无冲突；同版本同内容取 `updated_at` 新者，也不算冲突。
- 同版本内容不同即冲突，按策略裁决；`version`/`updated_at`/`source` 不进冲突字段列表。
- 墓碑带版本参与比较：删除版本更新则删除胜，旧版本更新无法复活记录；同版本删改交叉算冲突。

接口与策略：
- `merge(local, remote, strategy="remote", resolver=None)` 返回 `MergeResult`（dict 子类），
  附 `.conflicts`（`Conflict` 列表：field/local/remote/winner/reason）和 `.winner`。
- `strategy`：`"local"` 本地优先、`"remote"` 远端优先、`"manual"` 人工；
  人工时 `resolver({"local", "remote", "fields"})` 必须返回 `"local"` 或 `"remote"`。
- 两端须同时带或同时不带 `version`；都不带时退化为远端覆盖（兼容旧行为）。
- 异常路径显式抛错：未知策略、元数据缺失或类型非法、两端版本不对称、
  manual 缺 resolver、resolver 返回值非法，抛 `SyncError`/`ConflictError`，不静默跳过。
