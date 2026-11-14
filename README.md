# proxycircuit

按 (站点, 出口) 维度的三态熔断器，纯 Python 标准库。

接口：acquire(site) 取可用出口，report(site, proxy, ok) 上报结果。

## 阈值与冷却

- circuit.FAILURE_THRESHOLD：同一 (站点, 出口) 连续失败次数，达到后 closed -> open，默认 3。
- circuit.COOLDOWN_SECONDS：open 冷却时长，默认 30 秒；冷却结束后下一次 acquire 只放行一个探针进入 half_open。
- 探针成功 -> closed 并清零失败计数；探针失败 -> open 并重新计时。
- 状态按 (站点, 出口) 隔离，A 站故障不影响 B 站。

## 状态输出

- transitions()：状态迁移记录 (site, proxy, old, new, ts)。
- probe_results()：半开探针结果 (site, proxy, ok)。
- state_counts()：各态 (站点, 出口) 数量；state_of(site, proxy) 查单个状态。
- 非法参数（空站点、未知出口、非 bool 的 ok）抛出明确异常，不静默跳过。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
