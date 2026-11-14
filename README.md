# schemaversion

数据表结构的版本注册模块，纯 Python 标准库：版本链、向前兼容检查、破坏性变更审批、按版本解析与回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 版本口径

- `register(version)` 保留原入口，字段定义支持 `"int"` 旧写法和 `{"type": "str", "default": ""}` 新写法，每次注册记录字段、类型、默认值。
- 版本号必须为整数且严格大于历史最大版本（单调递增）；回滚只移动当前指针，不删版本链，之后注册的新版本号仍须超过历史最大值。
- `resolve(version=None)` 按版本解析结构，不传返回当前版本；回滚后任意已注册版本仍可读，未知版本抛 `UnknownVersionError`（也是 `KeyError`）。
- `rollback(version)` 回滚到已注册版本（含空基线 0），回滚点经 `rollback_points()` 查询。
- 注册与回滚均写审计：`audit()` 返回事件明细，同时输出到 `schemaversion.audit` 日志通道（可自行挂 FileHandler）。

## 兼容口径

注册前对照链上最新版本做向前兼容检查（首版为建立基线，无兼容性约束）：

- 向前兼容：新增**带默认值**字段、仅调整字段默认值，直接注册。
- 破坏性变更：删除字段、字段类型变化、新增**无默认值**字段。必须 `register(version, breaking=True, approver="姓名")` 显式标记并登记审批人，否则抛 `IncompatibleSchemaError`；审批人缺失或为空白抛 `ValueError`。
- `chain()` 返回版本链与每版变更明细，`report()` 输出版本链、兼容结论和回滚点的文本摘要。
