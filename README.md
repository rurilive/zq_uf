# crawllease

采集任务租约：带 TTL 的领取、持有者校验的续约、到期接管、幂等释放、FIFO 等待。

- `acquire` 领取带到期时间与版本号（fencing token），他人持有中拒绝或排队。
- `renew` 仅持有者可续约，过期/非持有者返回 `renewed=False`。
- 到期由后台 reaper（或 `sweep`）判定，`takeover` 接管并记录原持有者。
- `release` 幂等，重复/非法/不存在均不抛异常；释放后队首立即获得租约。
- 到期前 `on_expiring` 提醒一次，续约后重置。
- `status` 按任务查看，`history`/`takeover_records` 可查询回看，`export_takeovers` 导出 JSON。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
