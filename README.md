# schemagate

现状：结构检查一律放行，变更直接应用到全部表。

问题：下游取数才报错，无法按表回滚，变更没有审批。

目标：破坏性变更阻断，兼容变更按表灰度，失败自动回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 门禁与灰度口径

- `check(old, new)`：删列、收窄类型判破坏性并阻断，附迁移建议；加列、放宽类型（int→float→text）判兼容；兼容变更也留痕，结果可经 `export` 字段导出 JSON。
- 结构版本号全局单调递增，回滚恢复到上一版本。
- `apply(plan)`：plan 带 `check` 结果、`gray_ratio`、`approver`、`reason`；审批人、原因、变更摘要写审计。
- 兼容变更先按 `gray_ratio` 对表灰度，灰度期间新旧结构并存、读写兼容，成功后放量全量。
- `fail_tables` 模拟灰度失败：自动回滚灰度表到上一版本，全量不生效。
- 门禁、灰度、回滚、审批全部留痕于 `audit`，可用 `audit_log()` 查询。
