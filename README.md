# etlconf

配置驱动的 ETL 转换层，纯标准库。接口：`transform(rows, plan)`，plan 为纯 JSON 可序列化 dict。

## 步骤（默认顺序 strip → cast → fill → rename → derive，可用 `steps` 裁剪/重排）
- `strip: [字段]` 去首尾空白；`cast: {字段: "str"|"int"|"float"}`，空值转为 None
- `fill: {字段: 值}` 只填空值（None、`""`、`nulls` 声明的标记）；`0`/`"0"` 是零值，不填
- `rename: {旧名: 新名}`；`derive: {新字段: "qty * price"}`（仅算术表达式，加载期校验）

## 冲突与覆盖
- 同名字段走同一份规则；rename 目标重复、fill 与 cast 类型不符、表达式非法等在加载时抛 `PlanError`
- `overrides: {数据源: 片段}` 按数据源覆盖：字典段按键合并、列表段整体替换；用 plan 里的 `source` 键或 `transform(rows, plan, source=...)` 选择，未命中回退基础规则

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
