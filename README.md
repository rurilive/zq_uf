# formdiscover

通用表单发现：解析 action、method 和全部 input，区分可见字段、隐藏字段和提交按钮，
识别 CSRF 与一次性 token，并沿提交后的跳转（`next`）继续发现多步骤流程。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 步骤定义

- 入口 `discover(page, max_steps=10)`，`page` 为 dict：`action`（必填）、`method`（默认 get）、`inputs`（list）、`next`（可选）。
- `next` 是提交后跳转到的下一页 dict，结构同上，逐层跟随构成步骤链。
- 单页返回首步结果；多步骤额外带 `steps` 列表，每项含 `step` 序号及该步完整解析。
- 步骤数超过 `max_steps`（默认 10）判定为循环跳转，抛 `FormDiscoverError`。

## 字段规则

- 每个 input 必须有 `name`；`type` 缺省按 `text`，未知类型一律按可见字段处理。
- 分类：`hidden` → `hidden`；`submit/button/image/reset` → `submit`；其余 → `fields`（可见）。
- 字段含 `type`、`kind`、`required`（必填标记）、`token`；有 `value` 时保留。
- token 识别按字段名：`nonce|otp|one-time` 为 `onetime`，`csrf|xsrf|authenticity|token` 为 `csrf`，命中字段汇入 `tokens`。
- `method` 仅允许 `get/post`；结构不合法（缺 action、input 无 name、next 非 dict 等）一律抛 `FormDiscoverError`，不静默跳过。
