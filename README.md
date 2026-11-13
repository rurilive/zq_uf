# schemacheck

采集结果入库前的纯标准库校验：坏记录不阻断整批，单独隔离并带行号、字段、
原值和原因；按数据集配置错误率预算，超预算整批拒绝；支持流式校验与样本重放。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

- `validate(rows, rules)`：批量校验，返回 `{"ok": [...], "bad": [...]}`，
  可选附带 `"skipped"`、`"budget"`、`"batch_rejected"`。
- `validate_stream(rows, rules)`：流式校验，逐条产出
  `("ok", row)` / `("bad", entry)` / `("skipped", entry)`，
  结尾产出 `("summary", {...})`，不整体加载，适合大文件。
- `replay(samples, rules)`：重放拒绝样本（`bad` 条目自带原始记录）。

`bad` 条目含 `row`（行号）、`field`、`value`（原值）、`reason`、
`data`（原始记录，可直接重放）；必填缺失时为 `row` + `missing`。

## 规则语法

```python
rules = {
    "dataset": "users",              # 数据集名，用于匹配预算
    "required": ["id", "name"],      # 必填字段（空值视为缺失）
    "fields": {                       # 单字段规则，键即字段名（也作规则 id）
        "age":   {"type": "int"},                       # 类型
        "name":  {"length": {"min": 1, "max": 8}},      # 长度
        "role":  {"enum": ["admin", "user"]},           # 枚举
        "email": {"regex": r"[^@]+@[^@]+",              # 正则（fullmatch）
                  "depends_on": ["age"]},               # 依赖：前置失败则跳过并标记
        "dept":  {"reference": {"dataset": "depts",     # 跨数据集引用
                                "key": "id"}},
    },
    "checks": [                       # 显式规则列表（跨字段等）
        {"id": "date_order",
         "cross_field": {"left": "start", "op": "<=", "right": "end"}},
    ],
    "datasets": {"depts": [{"id": "D1"}]},   # 引用校验的参照数据
    "budget": {"users": 0.2, "default": 0.0},  # 按数据集的错误率预算
}
```

- `type`：`str` / `bool` / `int` / `float` / `number` / `list` / `dict`；
  `int`、`float` 接受可解析的字符串（如 `"30"`）。
- `length`：`{"min": n, "max": m}`，均可选。
- `cross_field`：`op` 为 `== != < <= > >=`，比较同行两个字段。
- `reference`：字段值须存在于 `datasets` 对应数据集中；`key` 指定
  参照记录的字段，省略时按标量集合匹配。
- `depends_on`：依赖的规则 id 列表；前置失败（或被跳过）时本规则跳过，
  记录到结果的 `"skipped"` 中，不计入错误率。

## 错误率预算

`budget` 可以是单个比率（`0.2`）、`{"max_error_rate": 0.2}`，或按数据集
的映射 `{"users": 0.2, "default": 0.0}`（按 `rules["dataset"]` 匹配，
缺省回落 `default`）。错误率 = 坏行数 / 总行数：

- 未超预算：只隔离坏记录，`ok` 正常入库，`budget.exceeded` 为 `False`。
- 超预算：整批拒绝，`ok` 清空并置 `batch_rejected: True`，
  `bad` 仍保留全部失败明细供诊断与重放。

