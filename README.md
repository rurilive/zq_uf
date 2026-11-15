# capticket

验证码工单：按优先级分派，同账号同时一张工单，超时回收可转派，回写确认一次。

- 分派：`submit` 入队，`dispatch` 取优先级最高者（同级先到先派）；账号已有进行中工单或在站点冷却期会抛 `TicketError`。
- 超时：工单超过 `timeout` 未回写，`sweep` 回收并释放账号，账号在该站点冷却 `cooldown` 秒（按站点区分）；`expired` 工单可 `reassign` 转派（可换账号），新工单保留原历史。
- 回写：`resolve` 与原任务对账（`expect`/`code_len`/`code_kind`），对不上抛错且不消耗工单；确认后流程只继续一次，重复回写报错；转派工单的回写 `resume=True`。
- `status()` 输出队列、超时工单、回写日志与冷却。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
