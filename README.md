# loginflow

可编排的多步登录流程：步骤声明动作、成功特征与超时，失败按策略重试或转人工。

## 步骤定义

`Step(name, action, expect, timeout, retries, backoff, on_failure, needs, provides, captcha)`

- `action(ctx) -> dict`：执行动作，返回的字段合并进上下文。
- `expect`：成功特征，字段名 / `{字段: 值或断言}` / `callable(ctx)`。
- `needs` / `provides`：声明依赖，乱序传入也会按依赖排序执行。
- `on_failure`：重试耗尽后 `abort` 或 `manual`（转人工回调）。
- `captcha=True`：执行前先调用验证码回调，结果写入 `ctx["captcha_code"]`。

## 回调格式

```python
run(account, steps, session_dir="sessions", callbacks={
    "captcha": lambda ch: input(f"${ch['account']}@${ch['step']} 验证码: "),
    "manual":  lambda step, reason: None,  # 返回 dict 则视为人工已修复
})
```

每步报告耗时、结果、重试次数与失败原因（`result.steps`）；日志只含步骤元信息，
不写凭据。会话按账号落盘 `sessions/<account>.json`，仅登录成功时写入，并过滤密码类字段。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
