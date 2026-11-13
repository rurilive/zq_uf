# taskqueue

1. `push(task, priority=100, flow_name="default")` 入队，数字越小优先级越高。
2. `lease(worker, flow_name)` 只领取指定流程任务，同优先级按入队顺序返回。
3. 租约到期前可用 `renew_lease(task, worker)` 续约，未续约任务会被自动回收重排。
4. 成功调用 `complete(task)`，可恢复失败用 `fail(task)`，不可恢复失败传 `retry=False`。
5. 状态机：`pending → running → success / failed`；可重试失败或租约超时回到 `pending`。
6. 重试达到 `max_attempts` 后进入 `dead_letter`，不再参与调度。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
