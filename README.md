# blockvote

按站点采集封禁信号的纯标准库决策模块：多信号加权投票、阈值熔断、观察态低频探针恢复、带过期时间的人工白名单。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 信号与权重

五类负面信号按站点分别计票，成功信号 `200` 不投票，仅在观察态作为探针结果：

| 信号 | 含义 | 权重 |
| --- | --- | --- |
| `403` | 明确封禁 | 4 |
| `captcha` | 验证码拦截 | 3 |
| `empty` | 空页面 | 2 |
| `ratelimit` | 限速 | 2 |
| `redirect` | 异常跳转 | 1 |

权重口径：封禁确定性越高权重越大。单个 `403` 权重 4 即达到阈值，保持"403 即熔断"的旧行为；单个 `captcha`（3）或任意更弱信号不熔断，需要多信号累计，解决一次误判停整站和信号互相矛盾的问题。

## 阈值与时间窗口

模块级常量定义在 `blockvote/block.py`，可按站点规模调整：

| 常量 | 默认值 | 口径 |
| --- | --- | --- |
| `TRIP_THRESHOLD` | 4 | 投票窗口内加权票数 `>=` 阈值才从 `closed` 熔断到 `open` |
| `VOTE_WINDOW` | 300 秒 | 滑动窗口，窗口外的票自动过期，票数随时间衰减 |
| `COOLDOWN` | 60 秒 | `open` 持续时间，冷却结束才允许进入观察态 |
| `PROBE_INTERVAL` | 30 秒 | 观察态两次探针的最小间隔，实现低频探测 |
| `PROBE_SUCCESS_REQUIRED` | 3 | 连续探针成功次数，达到后解除熔断 |

## 状态迁移

- `closed`（正常）：窗口累计票数达到阈值 -> `open`。
- `open`（熔断）：`allow` 一律返回 `False`；冷却结束后下一次 `allow`/`observe` 迁移到 `half_open`。
- `half_open`（观察态）：按 `PROBE_INTERVAL` 低频放行探针；`200` 累计连续成功，达到 `PROBE_SUCCESS_REQUIRED` 回到 `closed`；期间收到任何负面信号即探针失败，计数清零并退回 `open` 重新冷却。
- 熔断不会永久停用：没有探针成功就持续停在 `open/half_open` 循环，恢复必须由连续成功探测证实。

## 白名单

`whitelist(site, ttl)` 人工加白：ttl 秒内 `allow` 恒为 `True`，负面信号仍记录票数事件但不触发熔断；加白立即解除当前熔断并清空累计票。到期后自动失效，重新按投票规则判定。`unwhitelist(site)` 可提前撤销，`is_whitelisted(site)` 查询状态。

## 接口

核心接口签名保持不变：

- `observe(site, signal)`：上报信号，参与投票或探针判定，返回当前站点是否处于熔断态。
- `allow(site)`：当前请求是否放行（白名单、状态机、探针间隔共同决定）。

观测与运维接口：

- `tally(site)` / `votes(site)`：当前窗口加权票数与票明细。
- `state(site)`：`closed` / `open` / `half_open`。
- `probes(site)`：探针结果明细（时间、信号、是否成功、连续计数）。
- `events(site=None)`：事件日志，含 `vote`（票数）、`transition`（状态迁移）、`probe`/`probe_skipped`（探针结果）、`whitelist` 等。
- `set_listener(func)`：注册实时事件回调；`set_clock(func)`：注入时钟便于测试；`reset()`：清空全部状态。
