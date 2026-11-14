# schemadiff

表结构差异的影响分析模块，纯 Python 标准库。接口为 `diff(old, new)` 和 `impact(diff_payload, lineage)`，
比较字段名、类型、默认值、可空和主键，输出差异项、级别、影响表和修复建议，差异项为可 JSON 导出的字典并按优先级排序。

## 破坏性分级

- `critical`：字段删除、主键移除、同名字段类型变化（单独标记为 `type_changed`），下游必须改造。
- `major`：新增非空无默认值字段、可空收紧为非空、默认值移除、新增主键约束，需要回填或迁移。
- `minor`：普通新增字段、非空放松为可空、默认值新增或变更，通知下游即可。

## 影响口径

- 血缘支持 `{"表.字段": ["下游表.字段"]}` 字典或 `{"from": ..., "to": ...}` 边列表两种形式。
- `impact` 沿血缘从变更字段做遍历，输出完整影响路径 `paths`、受影响表 `affected_tables` 和字段 `affected_fields`。
- 无血缘或无下游命中时返回空列表；差异项无下游影响时不出现在结果中。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
