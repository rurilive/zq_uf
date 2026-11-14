# 变更留档：窗口化幂等去重（2026-11-15）

## 背景问题
- 同一指标重复上报，报表翻倍。
- 乱序到达时旧值覆盖新值。
- 去重集合无限增长，长跑内存爆。

## 方案
- 状态结构：`_seen` 由全量 set 改为 `{指标键: 最新已写事件时间戳}`。
- 判重：同键且 `ts <= 已存最新 ts` → duplicate，只计数不写入；同键更新 ts → 写入并保留最新。
- 窗口：按事件时间滑动，水位 = 状态中最新 ts；`ts <= 水位 - window_seconds`（默认 60s）的迟到数据计 expired，不写入。
- 清理：每次写入后清理窗口外状态；状态上限 `max_size`（默认 10000）；两类淘汰均最旧优先，数量计入 expired。
- 持久化：每次状态变更后 JSON 原子落盘（tmp + os.replace），模块加载时自动恢复；`configure(state_path=None)` 可关闭。
- 接口保持 `put(metric)` / `flush()`；flush 输出 `written / duplicate / expired / size`，重复计数单独字段。

## 兼容性
- 已有断言（`tests/test_dedup.py`）未改；`_seen`/`_written`/`_dup` 内部名保留，setUp 的 `clear()` 对 dict 同样有效。
- `put` 返回值新增 `expired` 字段，`flush` 新增 `expired`/`size` 语义不变。

## 验证
- `python3 -m unittest discover -s tests`：原有 3 条 + 新增 7 条（窗口去重、乱序覆盖、窗口清理、迟到过期、上限最旧淘汰、落盘恢复）全部通过。
- `python3 demo.py`：演示重复上报、乱序保护、窗口清理、重启恢复四段。
