# datacontract

团队间数据交付的契约注册中心：声明字段、类型、可空、枚举和默认值，纯 Python 标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 用法

- `Registry().register(contract)`：注册带 `version` 的 `Contract`（或含 `version` 的 dict）；旧式 `{"name", "fields": [str]}` 行为不变。
- `registry.diff(old, new)`：两版 `Contract` 输出兼容报告；旧式字段名列表仍返回 `{"added", "removed"}`。
- `registry.validate(name, rows)`：真实样本校验，错误带 1 起始行号。
- `registry.history(name)` / `registry.rollback(name, version)` / `registry.for_consumer(consumer)`：历史、回滚、按消费方查询。

## 兼容矩阵

| 变更 | 分级 |
| --- | --- |
| 新增可空/带默认值字段、类型放宽（int→float→str）、放宽可空、删除枚举约束 | 兼容 |
| 新增枚举值、改默认值 | 兼容（hints 提示） |
| 新增必填字段（不可空且无默认值） | 警告 |
| 删除字段、收紧类型、收紧可空、删除枚举值、新增枚举约束 | 破坏性（列出受影响消费方与字段） |

报告含 `compatible` / `hints` / `warnings` / `breaking` 四档，`verdict` 取最高级别。

## 拦截规则

- `register` 默认对破坏性变更抛 `BreakingChangeError` 拦截，版本不落库；`allow_breaking=True` 显式放行。
- `registry.check(contract)` 只出报告不注册，供提交流程（CI/pre-commit）接入。
