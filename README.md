# blockvote

站点封禁信号决策模块，纯 Python 标准库。五类信号按站点加权投票，
过阈熔断，熔断后低频探针恢复，并支持带过期时间的人工白名单。

## 权重与阈值口径

- 信号权重：`403=4`、`captcha=2`、`empty=2`、`ratelimit=2`、`redirect=1`。
- 熔断阈值 `BLOCK_THRESHOLD=3`：窗口内加权票数**严格大于 3** 才熔断。
- 因此 `403` 单次即熔断；弱信号需组合或累计，如 `captcha+ratelimit`（4 票）。
- 投票窗口 `VOTE_WINDOW_SECONDS=300` 秒，窗口外信号自动剔除；`200` 在正常态清空票数。

## 状态机与探针

- `closed` 正常放行 → 票数过阈迁移到 `open` 熔断停用。
- `open` 冷却 `COOLDOWN_SECONDS=60` 秒后，首个 `200` 进入 `half_open` 观察态。
- 观察态每 `PROBE_INTERVAL_SECONDS=30` 秒最多一次探针，连续 `RECOVERY_SUCCESSES=3` 次成功回到 `closed`。
- 观察态再收封禁信号则探针失败，立即退回 `open` 并重新冷却。

## 白名单

- `add_whitelist(site, ttl_seconds)` 人工加白，有效期内始终放行，信号旁路不投票。
- 白名单是覆盖层：不改动底层状态，过期后恢复底层熔断判定并记录 `whitelist_expired`。

## 接口

- 决策：`observe(site, signal)` 上报信号并返回是否停用；`allow(site)` 返回是否放行。
- 观测：`votes(site)` 票数明细、`status(site)` 状态、`stats(site)` 快照、`events(site=None)` 事件流。
- 事件同时走 `logging`（logger 名 `blockvote`），覆盖投票、状态迁移、探针结果与白名单。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
