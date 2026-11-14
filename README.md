# hostshield

按站点自适应限速：令牌桶控速率，按成功率与 RTT 动态调整令牌补充速率 `R` 和
并发上限 `C`；状态落盘并以文件锁在多进程间共享，每站独立，互不拖累。
纯 Python 标准库实现。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

```python
permit = acquire(site)                 # 非阻塞申请许可
info   = release(site, rtt, status)    # 归还许可并上报 RTT（秒）与 HTTP 状态码
shield.status(site)                    # 查看当前速率、并发上限与最近调整原因
shield.reset()                         # 清空全部状态（测试用）
```

- `acquire` 返回 `at`（虚拟调度时刻）、`wait`（墙上时钟建议等待秒数）、
  `granted`（并发或令牌不足时为 `False`，调用方退避后重试）。
- `release` / `status` 返回当前 `rate`、`concurrency`、`tokens`、`rtt_ema`、
  `success_rate` 及最近 5 条调整原因 `reasons`。
- 参数非法（空站点名、负 RTT、非 100..599 的状态码）抛 `ValueError`。
- 状态文件默认 `~/.hostshield/state.json`，可用环境变量
  `HOSTSHIELD_STATE_FILE` 覆盖；每次操作持 `fcntl.flock` 排他锁并原子写回。

## 模型与调整公式

每站独立维护一个令牌桶，桶容量等于并发上限 `C`（默认 4，范围 1..16），
令牌按速率 `R`（默认 1.0/s，范围 0.1..10/s）持续补充：

```
tokens = min(C, tokens + R × Δt)          # acquire 时按距上次的时间补充
许可间隔 interval = 1 / R                  # 发放时刻 next_at += interval
```

`acquire` 仅在 `in_flight < C` 且 `tokens ≥ 1` 时发放许可；`release` 归还
一个在途占用的令牌。

RTT 用指数滑动平均跟踪，首个成功请求同时作为基线 `rtt_base`：

```
rtt_ema ← α·rtt + (1−α)·rtt_ema           # α = 0.3
```

每次 `release(site, rtt, status)` 对该站独立应用以下规则：

1. **连续硬故障降速**：`status == 429` 或 `status >= 500` 记一次连续故障，
   连续达到 `N=2` 次立即降速并清零计数：
   `R ← max(0.1, 0.5·R)`，`C ← max(1, C−1)`。
2. **RTT 高压降速**：成功且 `rtt_ema > 2·rtt_base` 时
   `R ← max(0.1, 0.9·R)`，`C ← max(1, C−1)`。
3. **成功率窗口降速**：最近至多 20 次上报（满 10 次才评估）成功率
   严格低于 50% 时按规则 1 的因子降速。
4. **缓慢恢复**：连续 5 次成功、且 `rtt_ema ≤ 1.5·rtt_base` 时
   `R ← min(10, 1.1·R)`，`C ← min(16, C+1)`，成功计数清零。
5. 任意两次调整之间至少间隔 3 次上报（冷却期 `cooldown=3`），硬故障降速
   不受冷却期限制；所有结果都夹在各自的上下限内。

## 多进程共享

全部每站状态（令牌、并发占用、虚拟时钟、统计窗口、调整原因）保存在同一个
JSON 文件中。每个 `acquire`/`release` 先取排他文件锁再读改写，因此多个进程
合计发放的许可数仍受该站 `R` 与 `C` 约束，不会各算各的而合计超速。
