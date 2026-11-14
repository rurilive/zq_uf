# metricrollup

纯 Python 标准库的指标上卷模块：按事件时间落自然小时窗口，支持迟到
数据幂等重算，分位数用可合并的 t-digest 草图而不是直接平均。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 窗口口径

- 窗口按**事件时间**（metric 的 `ts`，秒级时间戳）划分，与到达顺序无关；窗口起点 = `floor(ts / 3600) * 3600`。
- 整数时间戳下每个窗口是**闭区间** `[start, start + 3599]`，例如 `3599` 落在窗口 0，`3600` 落在窗口 3600，边界各归其窗、不重不漏。浮点时间戳按 `floor` 取窗（数学上等价于左闭右开的 `[start, start + 3600)`）。
- 同一批次事件时间跨小时时，按事件时间分别落到各自窗口。
- **迟到数据**：事件时间小于已观察到的最大事件时间（水位线）即为迟到。迟到事件写入后，受影响窗口依据保留的原始事件**整体重算**，因此无论事件按什么顺序到达，窗口结果都一致；重复重算结果不变（幂等）。

## 草图口径

- 计数 `count` 与求和 `sum` 天然可加，上卷时直接相加。
- 分位数（`p50/p95/p99`）由每个窗口保留的 t-digest 草图计算（`metricrollup/sketch.py`，压缩阈值 `delta=100`）。
- 窗口/维度上卷时**合并草图质心**，而不是把各窗口分位数做平均，因此不会因分布差异而失真。单窗观测数不超过 `delta` 时草图保留全部点，分位数查询是精确的，且“拆窗后合并”与“全量一个草图”严格相等；超过阈值后草图自动压缩，分位数为近似估计。

## 接口

- `add(metric)`：`metric` 为 `{"dim": 字符串或字符串元组, "ts": 秒级时间戳, "value": 数值}`，返回 `(dim_path, window_start)`。`dim` 传元组表示层级维度（如 `("cn", "shop1")`）。
- `query(dim, window=None)`：`dim` 为字符串或路径元组，父路径会合并其全部子维度。`window` 可省略（全部窗口）、传小时对齐的起点（单个窗口），或传 `(start, end)` 闭区间。返回计数、求和、逐窗口明细、迟到重算次数、水位线和合并后的草图状态。
- `recompute_all()`：强制重算全部窗口，返回重算窗口数；`reset()` 清空状态。

输出中每个窗口带 `window_start/window_end`、`count/sum`、`p50/p95/p99`、`recomputations` 和该窗口 `sketch`（含 `algorithm/delta/size/total_weight/centroids`）；顶层另带全局 `late_recomputations`、`watermark` 与合并草图。

## 异常路径

- `add`：非字典抛 `TypeError`；缺 `dim/ts/value` 抛 `KeyError`；`dim` 非非空字符串/字符串元组抛 `TypeError`；`ts`、`value` 非有限数值抛 `TypeError`。校验失败不写入任何状态。
- `query`：维度（在指定窗口内）无数据抛 `KeyError`；窗口起点未对齐小时或区间 `end < start` 抛 `ValueError`；`window` 类型非法抛 `TypeError`。
