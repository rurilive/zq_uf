# crawlbackoff

纯标准库实现的采集退避协调模块。按**站点**共享退避：同站点的所有
采集任务（含不同进程）共用一个退避窗口和请求预算，任一任务命中
429 即全站降速；窗口结束后按阶梯恢复，连续成功才回满速。

## 接口

```python
from crawlbackoff import signal, budget

signal(site, 429)   # 上报一次请求结果；仅 429 触发退避，其他状态视为成功
budget(site)        # 查询站点当前窗口、剩余预算与恢复阶段
```

`budget` 返回字段：`phase`（normal/backoff/recovery）、`level`、
`remaining`（窗口内剩余预算）、`budget`（当前级别的窗口总预算）、
`window_end`、`window_seconds`、`window_remaining`、
`consecutive_successes`。

## 退避窗口口径

- 任一任务对某站点上报 429：全站立即进入 `backoff`，窗口 5s
  （重复触发翻倍，上限 300s），窗口内仅保留试探预算 3。
- 窗口内的成功请求只消耗试探预算，不累计恢复进度；再次 429
  重新计时并清零恢复进度。
- 窗口结束不直接回满速，而是进入 `recovery` 阶梯：预算 3 →
  （连续 3 次成功）→ 预算 6 →（再连续 3 次成功）→ 满速预算 10。
- 恢复期间任何一次 429 都会清零连续成功计数、重新退避并加长窗口。

## 跨进程与重启

状态 JSON 落盘在 `CRAWLBACKOFF_HOME`（默认 `~/.cache/crawlbackoff`），
读写均经 `fcntl` 文件锁 + 原子替换。进程重启后继续尚未结束的窗口，
各进程看到的剩余预算和恢复阶段保持一致。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
