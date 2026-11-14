# blockvote

多信号加权投票的封禁决策模块，纯 Python 标准库。

## 权重与阈值口径

- 信号权重：403=3，captcha=2，ratelimit=2，empty=1，redirect=1，按站点累计。
- 票数 ≥ 3 进观察态（停流量）；≥ 5 熔断；单信号不足 3 票不影响放行。
- 成功信号（200/ok）即探针：间隔须 ≥ 1s，连续 2 次成功才解除并清空票数。
- 探针间隔失败信号会清零连续成功数并按权重重新计票，可再次熔断。
- `whitelist(site, ttl)` 人工加白，期内信号不计票，过期自动恢复计票。

## 接口与输出

- `observe(site, signal)` 上报信号，返回是否应停用；`allow(site)` 查询放行。
- `status(site)` 输出票数、票型明细、连续成功数、白名单过期时间。
- `transitions()` 输出状态迁移日志；`probes()` 输出探针结果（含被忽略的低频探针）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
