# csrftoken

采集表单的令牌管理模块，纯 Python 标准库。按作用域缓存令牌，支持并发单飞、
一次性消费、过期重取和刷新失败回退。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 作用域口径

- 作用域键为 `(page, step)`；`page` 可写成 `"页面:表单"` 区分同页多表单。
- `get(page, step)`：同域有未过期、未消费的令牌则命中复用；否则单飞抓取，
  同域并发只抓一次，其余线程等待并复用结果。
- `consume(page, step)`：一次性消费，并发调用只放行一个（`True`），其余 `False`；
  无令牌抛 `TokenError`，已过期抛 `TokenExpiredError`。
- `invalidate(page)`：使该页面所有步骤的令牌（含回退）失效，返回 `True`。

## 回退口径

- 令牌带过期时间（`ttl`，默认 300 秒）和一次性标记；过期或已消费即不可用。
- `refresh(page, step)` 强制重取；抓取失败时回退上一枚仍有效的令牌并告警
  （logging `WARNING` + 计入 `stats()["alerts"]`）。
- 上一枚令牌已过期或已消费时不算有效回退，抛 `FetchError`。
- `stats()` 输出命中 `hits`、重取 `refetches`、回退 `fallbacks` 和告警列表。
