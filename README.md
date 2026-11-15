# datacontract2

数据契约兼容检查模块，纯 Python 标准库。接口：`check(old, new)`，按读 / 写两个方向分别判断。

## 方向定义

- **读方向（read）**：新契约的读取方去读按旧契约写入的数据。
- **写方向（write）**：新契约的写入方写给仍按旧契约读取的下游。

顶层 `compatible` / `breaking` / `direction` 保持读方向语义（兼容旧调用），
完整双向结论在返回值的 `read` / `write` 子结果和 `changes` 明细里。

## 兼容规则

| 变更 | 读方向 | 写方向 |
| --- | --- | --- |
| 删除字段 | 破坏 | 破坏 |
| 新增可选字段 | 兼容 | 兼容（建议确认下游容错） |
| 新增必填且无默认值字段 | 破坏 | 兼容 |
| 类型放宽（如 int → text） | 兼容 | 破坏 |
| 类型收紧（如 text → int） | 破坏 | 兼容 |
| 默认值新增 | 建议 | 建议 |
| 默认值修改 / 移除 | 警告 | 警告 |
| 枚举删除值 / 新增约束 | 破坏 | 兼容 |
| 枚举新增值 / 移除约束 | 兼容 | 破坏 |
| 可选 → 必填 | 破坏 | 兼容 |
| 必填 → 可选 | 兼容 | 破坏 |
| 版本号未单调递增 | 警告 | 警告 |

类型宽严次序：`bool < int < float < text`，宽类型可容纳窄类型的值。

每条变更明细（`changes` 元素）带：`direction`（方向）、`level`
（breaking / warning / info）、`kind`、`field`、`path`（字段路径，嵌套用点分）、
`message`、`suggestion`。破坏性变更在 `migration_script` 里给出迁移脚本建议。

字段可以写成类型字符串（`"int"`）或完整字典
（`{"type": "int", "required": true, "default": 0, "enum": [...]}`），
嵌套对象用 `fields` 继续声明。

## 用法

```python
from datacontract2 import ContractHistory, check, export, matrix

result = check(old_contract, new_contract)
result["read"]["compatible"]    # 读方向结论
result["write"]["breaking"]     # 写方向破坏字段
result["migration_script"]      # 迁移脚本建议

print(matrix(old, new))         # 兼容矩阵（按字段的表格）
export(result, "report.json")   # 导出检查结果为 JSON

history = ContractHistory("contracts/")  # 历史契约保留，版本号单调递增
history.add(new_contract)
history.compare()               # 对比最近两版
```

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
