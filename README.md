# contractdiff

数据契约兼容性检查：按**读写方向**分别判定，类型、默认值、枚举、必填、字段增删全部校验，
每条结论带方向、字段路径和建议；支持历史版本回看，以及新规则影子扫描 + gate 验证后启用。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 兼容规则（旧契约 -> 新契约）

| 变更 | 读取方向 | 写入方向 | 级别 |
| --- | --- | --- | --- |
| 数值类型收紧（int -> short） | **破坏**：旧数据可能超范围，解析报错 | 兼容 | breaking |
| 数值类型放宽（int -> long） | 兼容 | **破坏**：按旧类型写入的列存不下新值 | breaking |
| 类型家族变更（int -> text） | **破坏** | **破坏** | breaking |
| 删除字段 | **破坏**：缺列 | **破坏**：写出的列无人接收 | breaking |
| 新增字段（可选/有默认） | 兼容（忽略未知列） | 警告：旧写入方不产出 | warning |
| 新增必填且无默认 | 兼容 | **破坏** | breaking |
| 枚举删值 | **破坏**：存量数据校验失败 | — | breaking |
| 枚举加值 | — | 警告：旧读取方不认识新值 | warning |
| 默认值变化（含无默认 -> 0） | 单独标记 | 单独标记 | info |
| 必填约束变化 | 单独标记；无默认转必填按写入破坏处理 | | info/breaking |

字段支持 `type / default / enum / required`，`record` 类型可嵌套，结论路径形如 `profile.city`。
字符串简写（`"int"`）与完整 dict 两种写法等价。

## 返回结构

```python
check(old, new)
# compatible / read_compatible / write_compatible
# breaking / warnings / info / compatible_items：按级别分桶的结论清单
# findings：全部结论，每条含 rule, direction(read|write|both), path,
#           severity, message, suggestion, old, new
# suggestions：破坏与警告项的（路径、方向、建议）清单
```

## 历史版本与新规则上线

```python
from contractdiff import History, RuleBook, Rule, evaluate_gate, enable_if_passed

history = History()
history.add("v1", contract_v1).add("v2", contract_v2).add("v3", contract_v3)
history.versions()          # 可回看，get() 返回深拷贝快照
history.diff("v1", "v3")    # 任意两版本对比
history.scan()              # 相邻版本批量扫描

book = RuleBook()
book.register(Rule("default.review", "默认值人工复核", False, rule_func))  # 影子模式
gate = evaluate_gate(book, "default.review", history, [
    {"from_version": "v1", "to_version": "v2", "expected": ["a"]},  # 人工判断
    {"from_version": "v2", "to_version": "v3", "expected": "none"},
])
enable_if_passed(book, "default.review", gate)  # 全部一致才启用，否则保持影子
```

新规则注册后默认 `disabled`，正式 `check` 不产出其结论；`evaluate_gate` 以
`include_disabled=True` 回放历史版本对，逐字段路径与人工判断比对，全部一致后才允许启用。
