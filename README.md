# flowstep

纯 Python 标准库的 RPA 步骤编排器：显式上下文传递（无全局变量）、断点续跑、
幂等跳过、业务错误码定位、可序列化流程定义与条件分支。入口保持 `run(flow, context)`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 步骤定义

每个步骤是一个 `Step`，处理器签名为 `handler(ctx) -> dict`，只能从 `ctx` 读输入、
用返回字典产出输出，不允许使用全局变量传值。

```python
from flowstep import Step, Branch, registry, fail, run, dumps, loads

@registry.register                       # 或 registry.register(func, name="...")
def reserve_order(ctx):
    if ctx["amount"] <= 0:
        fail("INVALID_AMOUNT", field="amount")   # 业务错误：错误码 + 字段名
    return {"order_no": "O-1"}

flow = [
    Step("reserve_order",                 # 流程内唯一步骤名，也是检查点键
         handler="reserve_order",          # 字符串=注册表查找；也可直接传函数
         inputs=["amount"],                # 运行前校验，缺失报 INPUT_MISSING
         outputs=["order_no"],             # 运行后校验，缺失报 OUTPUT_MISSING
         idempotent=False,                 # False=写操作，完成后记录标记
         when={"field": "enabled", "op": "truthy"}),  # 可选步骤级条件
    Branch([                              # 条件分支：命中第一个 when，None=默认
        ({"field": "amount", "op": "gt", "value": 1000},
         [Step("manager_approve", idempotent=True)]),
        (None, [Step("auto_approve", idempotent=True)]),
    ]),
]
text = dumps(flow)                        # 流程定义 -> JSON 字符串
flow = loads(text)                        # JSON 字符串 -> 流程（处理器按名解析）
```

条件 DSL（纯数据，可 JSON 序列化）：`{"field": "a.b", "op": "gt", "value": 1}`，
操作符 `eq/ne/gt/ge/lt/le/in/not_in/contains/exists/truthy`，默认 `eq`；
组合条件用 `{"all": [...]}`、`{"any": [...]}`、`{"not": {...}}`。

## 断点续跑与幂等

检查点保存在上下文保留键 `__flowstep__` 中（`completed` 完成列表、`failed` 错误、
`runs` 次数），把上一次 `run` 的结果（或其 JSON 持久化副本）作为新 `context` 传入即可续跑：

- `idempotent=False` 的步骤成功后写入完成标记，重跑时直接跳过，写操作不重复执行。
- `idempotent=True` 的步骤不记标记，每次重跑都重新执行（如查询、校验、刷新页面）。
- 失败步骤不会写入完成标记，修正数据后重跑会从该步骤继续。
- 仅由裸函数组成的旧版 `run([fn1, fn2], ctx)` 保持原语义，不写检查点。

## 错误码

`run` 返回 `FlowResult`（dict 子类，旧的字典断言不受影响）：

| 属性 | 含义 |
| --- | --- |
| `ok` | 流程是否全部成功 |
| `error` | `{"step", "code", "field", "message"}`，成功为 `None` |
| `failed_step` / `code` / `field` | 失败步骤名、业务错误码、出错字段名 |
| `completed_steps` | 已完成（跳过依据）的非幂等步骤名 |

引擎内置错误码：

| 错误码 | 触发时机 | `field` |
| --- | --- | --- |
| `INPUT_MISSING` | 声明的输入字段不在上下文中 | 缺失字段名 |
| `OUTPUT_MISSING` | 返回值缺少声明的输出字段 | 缺失字段名 |
| `STEP_FAILED` | 调用 `fail()` 时未显式给错误码的默认码 | `fail` 传入的字段 |
| `STEP_RAISED` | 处理器抛出未预期异常，错误信息在 `message` | `None` |
| `BAD_RESULT` | 处理器返回值不是字典 | `None` |

业务步骤通过 `fail(code, field, message)` 抛出自定义错误码（如 `BUDGET_EXCEEDED`），
流程定义问题（重名、未注册处理器、非法条件）抛 `FlowDefinitionError`。
