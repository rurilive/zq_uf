# ruletemplate

数据集质量规则的模板层，纯 Python 标准库。公共模板定义通用规则，数据集只写字段绑定和差异。

## 继承和覆盖顺序

规则按 `(field, name)` 匹配，后者覆盖前者：

1. 父模板（`RuleTemplate(parent=...)`，可多级）
2. 子模板
3. `bind_dataset(overrides=...)` 数据集覆盖
4. `apply(dataset, rules)` 调用方规则

模板规则用逻辑字段名，`bind_dataset(fields={...})` 把逻辑字段映射到数据集实际字段。冲突（同一来源重复、覆盖与模板同名规则字段不匹配）在 `register_template` / `bind_dataset` 加载期抛 `ConflictError`，携带模板、字段和来源。

## 版本、迁移和回测

模板按 `(name, version)` 注册。`apply` 输出最终规则、覆盖链（`override_chain`）和相对最新版本的迁移差异（`migration`）；`migrate_dataset(dataset, to_version, rows=...)` 迁移绑定并可回测影响，`backtest(dataset, rows)` 单独跑回测。未绑定的数据集 `apply` 行为不变。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
