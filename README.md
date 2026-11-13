# captchahandoff

登录验证码接管模块：命中验证码时生成工单并暂停账号，人工回写后继续原流程。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口与工单状态

- `challenge(account, kind="image", ttl=None)`：命中验证码时调用。生成工单、暂停账号并抛 `CaptchaRequired`（带 `ticket_id`）；等待期内同账号重复调用只抛回同工单，不重复登录（账号互斥）。
- `submit(account, code)`：人工回写。成功则工单置 `resolved`、释放账号并返回结果 dict，原流程继续。
- `wait(account, poll_interval=0.5)`：阻塞等待回写；超时抛 `CaptchaTimeout`。
- 工单状态：`pending`（等待人工，账号暂停）→ `resolved`（已回写，流程继续）或 `expired`（超时失败，账号释放，需重新 `challenge`）。

## 超时口径与参数阈值

- 超时按工单计：`deadline = created_at + ttl`，用 `time.monotonic()` 计时；每次 `challenge`/`submit`/`wait` 触碰工单时惰性结算，`wait` 阻塞到回写或超时。
- `DEFAULT_TICKET_TTL = 300.0` 秒：工单默认超时，可用 `challenge(..., ttl=...)` 按工单覆盖；`WAIT_POLL_INTERVAL = 0.5` 秒为 `wait` 默认轮询间隔。
- 验证码校验：空码或占位码（全部相同字符，如 `0000`）视为无效输入，`submit` 抛 `CaptchaRequired` 且不消费工单。

## 异常路径（均明确报错，不静默跳过）

- 无待处理工单时 `submit`/`wait`：抛 `CaptchaRequired`。
- 工单已超时再 `submit`：抛 `CaptchaRequired`（`status="expired"`）；`wait` 等到超时抛 `CaptchaTimeout`，账号已释放。
- 空 `account`、非正 `ttl`/`poll_interval`：抛 `ValueError`；查询未知工单 `ticket_status` 抛 `KeyError`。
