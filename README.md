# logsample

现状：每条日志原样写入内存列表，没有采样，也没有保留策略。

问题：磁盘一天几十G，排查时需要完整样本却拿不到，采样后总量无法还原。

目标：分级采样，错误和慢请求全留，按站点和小时调比例并可还原总量。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 采样规则
- 接口保持 `write(record)`；未配置任何比例时全量写入、不加字段（兼容旧行为）。
- 错误（`status>=500` 或 `error` 为真）和慢请求（`slow` 为真或 `duration_ms>=2000`）全量保留。
- 正常请求按比例采样：`set_rate(site, rate, hour=None)`，精确 (站点,小时) 优先于站点，`"*"` 兜底。
- `set_target(site, n)` 动态模式：第 k 条正常请求按 `min(1, n/k)` 采样，保留量约 `n·(1+ln(N/n))`。
- 采样中的记录带上 `sampled / sample_rate / sample_reason / batch` 字段，决策可回放。
- `write_batch(records, keep_all=True)` 或 `pause()/resume()` 可临时关闭采样（异常批次全留）。
- `replay(batch_id)` 按批次回放保留的样本行。

## 估算口径
- 每个 (站点,小时) 键保留精确计数：`seen` 总数、`errors`、`slows` 精确无误差。
- 正常请求用 Horvitz-Thompson 权重还原：每条保留样本计 `1/rate`，总量估算 = 错误 + 慢 + Σ(1/rate)。
- `error` 为 1-sigma 标准差 `sqrt(Σ(1-rate)/rate²)`，真值约 95% 落在 ±2 倍误差内。
- `stats(site)` 按站点查询当前比例、精确计数、估算总量和误差。
