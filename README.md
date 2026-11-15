# formbatch

幂等批量表单提交：每条带幂等键，成功项不重提；站点 + 全局两级限速，支持并发；失败项按类别单独重试，结果逐条对账。纯 Python 标准库。

## 接口

- `submit(forms, *, sender=None, batch_id=None, **overrides)` → `{"batch", "results", "summary"}`
- `resume(batch_id, *, sender=None, **overrides)` → 只重提失败项；批次未知时返回 `{"resumed": False, "results": []}`
- `export_detail(batch_id, path=None)` → 逐条对账明细；`.csv` 结尾写 CSV，否则写 JSONL
- `configure(**kwargs)` 设置全局默认；`submit`/`resume` 的 overrides 可临时覆盖，另支持注入 `clock`/`sleep` 便于测试

`sender(form, seq)` 是可插拔的传输层：返回 `{"ok": False, "category", "message"}` 或抛 `SubmitError` 表示失败，默认 sender 直接成功。

## 幂等口径

- 幂等键取 `form["idempotency_key"]`，缺省为表单内容的 SHA-256。
- 提交成功后键登记在册，保留 `idempotency_ttl` 秒（默认 3600），到期自动清理。
- 保留期内同键再提交记为 `skipped`（`ok=True`），不再发送；同批次内键重复也跳过并标注 `duplicate`。
- 失败不登记，因此失败项在下次 `submit` 或 `resume` 时会被重提。

## 限速口径

- 两层控制：`global_rate`（全局每秒请求数）与 `site_rate`（单站点每秒请求数），`None` 表示不限；站点取 `form["site"]` 或 `form["url"]` 的 netloc。
- 限速器预约下一次允许发起的时间并睡眠等待；等待次数与秒数累计进 `summary.rate_limit_waits` / `rate_limit_wait_seconds`，服务端返回 `rate_limited`（如 429）的退避时间也计入。
- 并发由 `max_workers` 控制，限速在并发下依然生效。

## 失败与重试

- 失败类别：`network` / `timeout` / `rate_limited` / `server` / `unknown` 可重试，`client` 不重试。
- 每条最多重试 `max_retries` 次（默认 3），退避为 `backoff_base * 已尝试次数`。
- 每条结果的 `reason` 记录 `category`、`message` 和请求序号 `seq`；`results` 与提交的 `forms` 按下标逐条对齐。
- `summary` 输出成功、失败、跳过条数和限速等待。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
