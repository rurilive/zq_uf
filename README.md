# crawlprio2

采集队列：站点/租户两级配额 + 等待老化提权 + 有配额上限的高优插队。纯标准库。
接口保持 `push(task)` / `pop()`；默认配置为纯 FIFO（兼容旧行为），`configure()` 开启各机制。

## 口径

- 有效优先级 = `priority + min(等待秒数 × aging_rate, aging_cap)`；`aging_cap=0` 关闭提权。
- 配额：`site_quota` / `tenant_quota` 限制同一站点/租户**连续**出队次数，分别统计；
  触限后跳过队首取第一个未触限任务（reason=`quota_skip`）；全队同源时放行，避免饿死。
- 插队：有效优先级 ≥ `preempt_threshold` 且严格高于队首才可插队，同分保持 FIFO；
  每站点每 `preempt_window` 秒限 `preempt_quota` 次，插队消耗配额；
  靠提权达标的插队记为 `aging_preempt`。
- 保底：每 `guarantee_window` 次出队，最长等待的低优任务（`priority ≤ low_priority`）优先出队。
- 超时：等待超过 `max_wait` 秒按 `timeout_action` 处理——`degrade` 进降级队列
  （主队列排空后出队，reason=`degraded`）或 `drop` 直接丢弃。
- 留痕：`pop()` 返回的 `dequeue` 字段带 reason/wait/boost；`dequeue_log()` 保留每条出队原因；
  `stats()` 汇总出队、配额（by_site/by_tenant/preempt_used）、提权与等待。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
