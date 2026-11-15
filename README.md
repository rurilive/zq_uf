# jsonschema2

采集响应的模式校验与降级模块，纯 Python 标准库。

## 校验口径

- **必填**：`required` 中缺失的字段报 `missing`。
- **类型**：按 `type` 校验 `object / array / string / integer / number / boolean / null`；
  `integer` 不接受 `bool`，`number` 接受整数。`type` 也支持列表（任一匹配即通过）。
- **枚举**：`enum` 中不含该值时报 `enum`。
- **嵌套**：递归进入 `properties` 与 `items`，数组元素逐个校验。
- **路径**：根为 `$`，对象字段 `$.a.b`，数组元素 `$.tags[1]`；错误定位到具体嵌套层级。

`validate(payload, schema)` 返回：

| 字段 | 含义 |
| --- | --- |
| `status` | `pass` / `degraded` / `isolated` |
| `ok` | 仅 `pass` 为 `True` |
| `errors` | 全部错误，含 `path / code / message / expected / actual / importance` |
| `degraded` | 已降级字段：`path / action / value / source / importance` |
| `isolated` | 隔离原因（仅 `critical` 错误） |
| `provenance` | 修复字段的来源标记 `{path, source}` |
| `path` | 首个错误的路径 |
| `payload` | 降级后的数据；隔离时为 `None` |

## 降级口径（按字段重要性）

字段级 `importance` 缺省规则：在 `required` 中为 `critical`，否则 `optional`。
`schema.importance` 可整体覆盖，根节点默认 `critical`。

| 级别 | 不合法时处理 |
| --- | --- |
| `critical` | 不可修复，整条记录隔离，`payload=None` |
| `important` | 替换为默认值，来源依次为 `schema_default` → `enum_first` → `type_default` |
| `optional` | 声明了 `default` 则替换（`schema_default`），否则丢弃该字段（`dropped`） |

- 必填字段即使显式标 `optional`，也按 `important` 处理，避免静默缺失。
- 任一 `critical` 错误即整体隔离，不做部分降级。
- 降级字段同时记入 `degraded` 与 `provenance`，修复后的数据可追溯来源。
- `validate` 不修改入参（深拷贝后处理）。

`repair(payload, schema=None)` 返回 `{payload, repaired, fields, status, isolated, provenance}`；
不传 `schema` 时保持原有 no-op 语义。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

`demo.py` 演示正常结构、可降级坏结构和不可修复坏结构三组响应。
