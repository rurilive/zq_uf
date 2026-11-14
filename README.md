# formcaptcha

验证码工单分派模块，纯 Python 标准库，单账号串行。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 分派口径

- `submit(account, task, priority=0, timeout=60.0)` 建单入队，`resolve(ticket, code)` 回写确认。
- 优先级数值大的先派，同优先级按到达先后（FIFO）。
- 同账号同时只允许一张待处理工单，再提抛 `AccountBusyError`；回写或超时后释放。
- 参数非法（空账号、非数值优先级、非正超时）抛 `TicketError`。

## 超时与回写口径

- 工单从建单起算 `timeout` 秒，到达 deadline 即超时；在下次 submit/resolve/快照时回收。
- 超时工单移出队列、释放账号，记入 `expired_records()`；对已超时工单回写抛 `TicketStateError`。
- 每张工单只确认一次，重复回写抛 `TicketStateError`；未登记工单号按人工线下回写记录。
- 输出：`queue_snapshot()` 当前队列、`expired_records()` 超时记录、`resolve_records()` 回写记录。
