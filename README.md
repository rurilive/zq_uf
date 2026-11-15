# crawlclock

采集调度时钟：纯 Python 标准库，无第三方依赖。调度时间按站点时区输出，
并按参考时间估计漂移、先告警再平滑校正，校正记录可落盘、可按机器查看。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

```python
from crawlclock import now, sync, configure

configure(station="America/New_York", machine_id="crawler-a",
          drift_threshold=1.0,   # 秒，超过先告警
          slew_rate=0.05,        # 每秒最多校正 0.05 秒
          deadband=0.01,         # 秒，小于此值视为无漂移
          log_path="logs/drift.jsonl")

now()   # -> 站点时区下、已校正的 aware datetime，单调不回退
sync(reference)  # -> dict：drift/corrected/timezone/offset/target_offset/
                 #         raw_drift/alerted/reachable/slewing/dst/at
```

`sync` 的参考时间接受：带/不带时区的 `datetime`、UTC epoch 数字、
ISO-8601 字符串，或返回它们的可调用对象（例如 NTP 客户端函数）。

## 校正口径

| 符号 | 含义 |
| --- | --- |
| `raw_drift` | 未校正的本地时钟 − 参考时间；正值表示本机偏快 |
| `drift` | **调度实际看到的偏差** = `(本地 + offset) − 参考时间`，可用于告警判断 |
| `offset` | 当前平滑校正量，`now() = 本地 + offset` |
| `target_offset` | 本次 sync 计算出的目标校正量，`sync` 后 `drift` 归零 |

- **漂移估计**：`sync` 用本地时钟与参考时间之差估计漂移，目标偏移取反。
  `|drift| <= deadband` 时视为无漂移，不校正。
- **先告警再平滑**：`|drift|` 超过 `drift_threshold` 时先写一条 `alert`
  记录并打 WARNING 日志，再按 `slew_rate` 逐步逼近目标偏移。告警按状态沿
  触发——跨过阈值时记一次，回落到阈值内记 `alert_cleared`，不会每次
  `sync` 都刷屏。
- **校正不回退时间**：`now()` 记录本次返回值，后续即使目标偏移向下调整
  或时钟源抖动，也不会输出早于上一次的时间。`slew_rate` 因此限制在
  `(0, 1)`，保证平滑校正的推进速度不超过真实时间。
- **参考时间不可达**：`None`、调用抛错或无法解析时，**保持上一次的偏移和
  漂移值**，不把漂移伪造成 0，写一条 `unreachable` 记录并记 WARNING。

## 时区口径

- 站点时区通过 `configure(station=...)` 配置，取 IANA 名称（如
  `Asia/Shanghai`、`America/New_York`）；未配置时尽量探测本机 IANA 时区，
  取不到则退回固定偏移。
- `now()` 始终返回**站点时区**下的 aware `datetime`。跨时区任务统一用
  `station_window(start, end)` 换算到站点时区计算，避免各自用本机时区
  得到不同窗口。
- **夏令时切换**：检测到站点 UTC 偏移变化时（`forward` 春季前拨 /
  `backward` 秋季回拨），以切换时刻为基准**重新锚定**，并给出切换点
  前后两个窗口（`dst["windows"]["before"|"after"]`，各长一个偏移变化量）
  以及 `gap`（前拨跳过的小时）或 `overlap`（回拨重复的小时）。切换只改变
  站点时区的本地显示，不改变漂移估计（漂移始终用 UTC epoch 计算）。
- 若进程离线跨过了多次切换（例如停机数月），`dst["transitions"]` 会列出
  区间内所有切换（前后窗口齐全），顶层字段取最后一次；一次检测只写一条
  `timezone` 记录。切换时长按实际偏移变化量计算，半小时制时区
  （如 `Australia/Lord_Howe`）也能正确处理。
- 切换信息写入 `timezone` 记录，并在下一次 `sync` 的 `dst` 字段中返回。

## 记录与查看

记录默认保存在进程内存，配置 `log_path` 后同步追加为 JSONL。

```python
records(machine="crawler-a")        # 按机器过滤，可选 event="sync"/"alert"/...
drift_curve(machine="crawler-a")    # 该机器的漂移曲线点（成功 sync）
load_records("logs/drift.jsonl")    # 读回文件，可与 drift_curve(records=...) 联用
status()                            # 当前 offset/漂移/告警状态快照
```

事件类型：`configure`、`sync`、`alert`、`alert_cleared`、`correction`、
`timezone`、`unreachable`。每条记录都带 `seq`、`machine`、`timezone`、`at`
（站点时区）、`epoch`（UTC），多机可合并后按机器筛选。

## 演示

`demo.py` 用可拨动的假时钟模拟一个比参考时间快 6 秒的机器：超过阈值先
告警、按 0.05/s 平滑校正到零漂移、跨夏令时切换给出前后窗口、参考时间
不可达时保持偏移，最后按机器查看漂移曲线与 JSONL 记录。
