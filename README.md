# schemamigrate

现状：只支持向前改版本号，没有校验和回退。

问题：升级后回不去，新旧版本并行字段对不上，半途失败留下混合版本。

目标：双向迁移，逐步校验，失败保留现场，支持回退点。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

版本链：`Migration(version, forward, backward)` 注册到 `Migrator`，`upgrade(v)` 按序执行
(current, v] 的 forward，`downgrade(v)` 逆序执行 backward；链上缺步或缺 backward 时
回退直接拒绝（NotImplementedError），升级对未注册版本视为无变更。

校验口径：每步前后记录行数和校验和（逐行规范化 JSON 排序后的 SHA-256，与行序无关）；
行数必须匹配 `row_delta`，可选 `check` 钩子复核。失败即恢复本步快照、停在该步，
现场存入 `result["failure"]["scene"]`，`rollback_point` 指向停住的一致版本。
并存期用 `aliases={新: 旧}`：`insert` 双写两侧字段，`read_field` 读时回退。
每次迁移在 `history` 记录版本、方向、操作人和耗时（duration_ms）。
