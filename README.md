# aggretract

纯 Python 标准库的增量聚合器，支持版本覆盖、撤回扣减、事件时间窗口和对账统计。

## 接口

- `Aggregator(allowed_lateness=0)`：`allowed_lateness` 单位为秒，缺省 0。
- `apply(batch)`：应用一批明细，返回 `totals`（key -> 聚合值）。
- `query(key, window=None)`：查 key 的聚合值；传入窗口 id 时只查该窗口。
- `stats(key=None)`：返回 `total`、`detail_count`、`late_count`、`retracted_count`，传 key 时附带各窗口明细。
- `Aggregator.window_for(event_time)`：事件时间对应的 UTC 日窗口 id（epoch 天）。

## 明细字段与版本口径

每条明细包含 `key`（聚合主键）、`id`（明细唯一键）、`version`（版本号，缺省 0）、`value`、`event_time` 和 `op`。

- 同一 `id` 按 `version` 处理：高版本覆盖旧版本，低版本或同版本直接忽略，因此重复消费同一批数据幂等，不会翻倍。
- `op="retract"`（或 `retract=True`）为撤回：按该 id 上一版的 `value` 从聚合扣减，并计入 `retracted_count`；撤回未知 id、重复撤回都不会让计数转负。撤回后更高版本的 upsert 可重新生效。
- 缺省 `op` 为 `upsert`；缺省 `id` 时自动生成，但自动 id 不保证跨实例重放，生产数据应自带唯一键。

## 窗口与迟到口径

- `event_time` 支持 epoch 秒或 ISO 8601 字符串（`Z` 按 UTC 处理）；窗口为 UTC 自然日，id 为 epoch 天。没有 `event_time` 的明细归入 `None` 窗口，不参与迟到判断。
- 水位线 = 已见最大事件时间 − `allowed_lateness`；事件时间不早于水位线即接收，并按事件时间归入对应窗口，乱序/迟到但在容忍度内的数据照常入窗。
- 事件时间早于水位线则丢弃，`late_count` 加一；被丢弃的高版本更新会先撤回它旧版本的聚合贡献。丢弃会留下同版本墓碑，重复投递不会重复计数。

## 对账

`stats` 的三项主口径可互相核对：窗口 `total` 之和等于 key 的 `total`；`detail_count` 是当前有效明细条数（更新不增条数，撤回减条数，计数下限为 0）；`late_count` 是累计丢弃条数。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
