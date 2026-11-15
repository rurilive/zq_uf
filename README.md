# proxymesh

采集出口的多机房选路模块，纯标准库。对外只暴露 `pick(site)` 和
`report(proxy, ok, rtt)`，其余（出口池、锁定、日志、统计）为可选辅助接口。

```bash
python3 demo.py                        # 模拟单出口故障、机房级故障与恢复
python3 -m unittest discover -s tests  # 24 条用例
```

## 选路口径

每个出口维护 **健康分**（0~1）和 **延迟 EWMA**，按机房分组汇总。

- 综合分 `score = health * (1 - w + w * latency_score)`，`w=latency_weight`
  （默认 0.4）。健康分是乘性上限，`score <= health` 恒成立：延迟再好也只是
  逼近健康分本身，所以“快但坏”的出口无法靠延迟补回健康分。
- `latency_score = 1` 当 `rtt <= latency_target`（默认 0.30s），否则
  `target / rtt`。尚未测过延迟的出口给乐观分 `latency_unknown`（默认 0.75），
  保证它会先被选一次拿到 rtt，而不是永远排在有历史数据的出口后面。
- 选路**确定性**优先：按分数降序，同分时保持当前出口，再按名字稳定排序；
  不再随机。
- 返回 `candidates`（每个出口的 `score`/`health_score`/`eligible`/冷却标记）
  与 `reason`/`reason_code`，`selection_log()` 保留最近 `log_limit` 条。
- 兼容字段 `health` 恒为 `None`，实际健康分看 `health_score`。

## 健康分口径

- 失败：`health *= failure_penalty`（默认 0.5），连续失败更快掉分。
- 成功：`health += (1 - health) * success_gain`（默认 0.35），逐步回量而不是
  一次性满血。
- 时间衰减：两次更新之间的空档按 `health = 1 - (1 - health) * exp(-Δt / decay_tau)`
  向 1.0 回填（`decay_tau` 默认 60s），久未出错的惩罚自然淡化。
- 延迟用 EWMA（`rtt_alpha` 默认 0.3），单次抖动不会立刻改变排序。

## 冷却与机房切换口径

- **出口冷却**：`fail_window`（默认 120s）内连续失败达到
  `proxy_fail_threshold`（默认 2）次 → 冷却 `proxy_cooldown`（默认 30s）。
- **机房冷却**：同一机房在窗口内连续失败达到 `room_fail_threshold`（默认 3）
  次，或该机房所有出口都进入冷却 → 整机房冷却 `room_cooldown`（默认 60s）。
- **冷却期出口不参与打分**：候选里 `eligible=False`、`score=None`，日志仍保留
  该候选及其冷却原因。全部出口都冷却时才走应急，按健康分选“最不坏”的一个，
  `reason_code=emergency`。
- **切换**：`report()` 失败后若当前出口已冷却或所在机房已冷却，自动重选并在
  返回值中给出 `switched=True` 与 `selected`，同时写入 `failover` 日志。
  `pick()` 的 `switched` 表示与上一次选中不同。单次失败只降权不切换。
- **手动锁定**：`lock_room(site, room)` 后自动切换不跨出该机房
  （`reason_code=locked`）；锁定机房内出口全冷却时应急留在机房内
  （`locked_emergency`），不会切到别的机房。`unlock_room(site)` 解除。

## 辅助接口

```python
from proxymesh import set_pool, stats, selection_log, lock_room

set_pool("shop", [{"proxy": "bj-1", "room": "beijing"},
                  {"proxy": "sh-1", "room": "shanghai"}])
stats("shop")            # 各机房 avg_health / best_rtt / cooling_room
selection_log("shop")    # 每次选路的候选与原因
```

未注册池时默认 `proxy-a`（机房 room-a）、`proxy-b`（机房 room-b）；
显式给 `{"proxy", "room"}` 时以它为准，只给字符串时机房名由出口名的
前缀推断。
`Mesh(clock=..., **config)` 可注入时钟与覆盖默认参数，便于测试。
