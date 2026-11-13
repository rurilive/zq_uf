# logsample

分级日志采样：错误和慢请求全量保留，正常请求按比例采样，总量可还原。

## 采样规则

- `write(record)` 接口不变；默认不采样、全量写入，`enable_sampling(rate)` 开启。
- `status >= 500` 或 `slow` 为真的记录始终全留，正常请求按比例系统抽样。
- `set_rate(rate, site=..., hour=...)` 按站点和小时动态调比例，`get_rate(site, hour)` 查询。
- 采样决策（`sampled`/`sample_reason`/`sample_rate`/`batch`）写进记录，`replay(batch)` 按批次回放。
- 异常批次用 `with sampling_off():` 临时关闭采样，退出后自动恢复。

## 估算口径

- 每个 `(site, hour)` 键保留精确计数，`stats(site)` 查总量与落盘量。
- 总量用 Horvitz-Thompson 推算：estimate = Σ(1/p_i) + 全留数；误差为 1σ = sqrt(Σ(1-p_i)/p_i²)。
- `estimate(site)` 按站点返回 `total`、`estimated_total`、`error`、`rate`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
