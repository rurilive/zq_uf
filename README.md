# fieldmerge

多来源按行一致的字段合并模块，纯 Python 标准库。每个来源一次性提交同一行的
一组字段；合并时以来源为单位整组打分、整组胜出，不再跨来源拼接字段。

## 输入与权重口径

接口保持 `merge(row_id, values)`，`values` 为 `{来源: 字段组}`；旧写法
`{"erp": {"amount": 10}}` 仍可用，可信度取缺省值 1.0、无时间戳。
带元信息的写法（`$` 前缀为保留键）：

```python
merge("row-1", {"erp": {"$confidence": 0.9, "$timestamp": 2000.0,
                         "fields": {"amount": 100}}})
```

## 权重与时间衰减

- 权重 = 可信度 × 衰减系数；可信度缺省 1.0，负值按 0 处理。
- 衰减系数 = `0.5 ** (年龄 / 半衰期)`，默认半衰期 3600 秒。
- 年龄 = 本行最大时间戳 − 该来源时间戳，非负；无时间戳的来源不衰减（系数 1.0）。
- 半衰期与缺省可信度可用 `configure(half_life=..., default_confidence=...)` 调整。
- 权重最高的来源整组胜出；同分时后提交的来源胜，并记 `source_tie` 冲突。

## 输出与冲突

返回 dict 子类，键为主记录字段：冲突字段取胜出来源的值，胜出来源没有的字段
（如独有字段）按最高权重补入。归因信息放在属性上：

- `winning_source` / `weight`：胜出来源及其权重。
- `sources`：每个来源的可信度、时间戳、衰减系数、权重和字段组。
- `candidates`：逐字段候选，含来源、值、可信度、时间戳、权重。
- `conflicts`：`source_tie`（同分）与 `field_conflict`（同字段不同值）。
- `rules`：规则命中流水（打分、衰减、同分、选主、字段冲突）。
- `has_conflict`：是否存在任一冲突。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
