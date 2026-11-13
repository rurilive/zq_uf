# watermark

乱序事件的水位与窗口聚合，纯 Python 标准库，接口为 `push(event)` / `advance(ts)`。

水位公式：`watermark = quantile(已见事件时间, quantile)`，单调不减；
窗口 `[s, s+size)` 在 `watermark > 窗口结束 + 允许延迟` 时关闭，输出保持到达顺序。

配置项（`Window(...)`）：
- `size`：窗口长度。
- `quantile`：水位分位，(0, 1]，默认 1.0（即最大时间戳）。
- `allowed_lateness`：允许延迟，数值或 `{source: 延迟}` 字典（`default` 键兜底）。
- `idle_timeout`：空闲超过该时长后 `advance(ts)` 按处理时间推进水位；默认 None 表示总是推进。

侧输出：窗口关闭后到达的事件进入 `window.side_output`，
每条记录 `event`、`window`、`lateness`（= watermark − 窗口结束）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
