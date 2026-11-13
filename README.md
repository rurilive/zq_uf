# proxycheck

出口（代理）健康管理：每个出口每轮连续探测多次，按成功率与延迟打分，
连续失败自动摘除，冷却后放回试探，试探成功再重新计入可用。纯 Python 标准库。

## 接口

- `check(pool, probe=None)` / `pick(site)`：保持历史签名，无状态；`check` 返回
  `{"proxy", "ok", "latency"}` 列表，`pick` 返回第一个健康出口。
- `HealthMonitor(probe, ...)`：带状态的健康管理器，跨轮维护摘除/恢复与打分，
  `monitor.check(pool)` 同构返回结果，`monitor.pick(results)` 按得分选出口，
  `monitor.snapshot()` / `monitor.report()` 输出每出口状态、成功率、延迟、最近错误。
- `probe(proxy)` 返回 `(ok, latency)` 或 `(ok, latency, error)`，抛异常按一次失败处理；
  内置 `tcp_probe(timeout)` 可直接探测 `host:port`。

## 阈值（HealthMonitor 参数，括号为默认值）

- `attempts`(3)：每个出口每轮连续探测次数。
- `fail_threshold`(3)：累计连续失败次数达到该值即摘除；失败按探测次数累加，
  任一次成功清零，因此部分成功的抖动出口不会被摘除，只会被打低分。
- `cooldown`(30s)：摘除后的冷却时间，冷却期内该出口跳过探测、不参与选择。
- `recovery_threshold`(1.0)：试探轮成功率达到该比例才恢复为健康，否则重新摘除并刷新冷却。
- `workers`(8)：探测线程数，所有出口的探测在线程池内并发执行。
- `round_timeout`(10s)：单轮巡检总耗时上限；截止时仍未完成的探测记为失败（timeout 错误），
  慢出口不会拖住整批任务。

## 打分与恢复策略

- 得分 = 成功率 x 0.8 + 延迟分 x 0.2，其中延迟分 = 1 / (1 + 平均延迟ms / 100)，区间 [0, 1]。
- 状态流转：unknown -> healthy -> ejected -> trialing -> healthy/ejected。
- 摘除满 `cooldown` 后，下一轮以 trialing 身份放回试探；试探轮成功率达标才恢复 healthy，
  不达标回到 ejected 并重新计时，避免坏出口反复冲击任务。
- 平均延迟取最近 20 个成功样本；`snapshot()` 额外给出累计探测/成功次数与连续失败数。

## 运行

```bash
python3 demo.py                      # 稳定 / 抖动 / 全坏三类出口的完整生命周期
python3 -m unittest discover -s tests
```
