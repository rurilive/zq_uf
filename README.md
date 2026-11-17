# permaudit
安全方向的小工具，按账号对比授权范围与实际使用，输出可收回权限。

- `audit(account)`：登记账号（系统/岗位/在职状态/授权/使用记录/最近登录），返回授权范围、未使用 `unused`、长期闲置 `stale`（阈值 `STALE_DAYS=90`）、异常账号（离职/转岗）和待收回清单。
- `report()`：按系统汇总；待收回清单按系统→岗位分组；长期未登录账号（`DORMANT_DAYS=180`）单独统计；列出待复核账号和按时间排序的变更记录。
- `grant(id, perm, actor, time)` / `reclaim(id, perms=None, actor, time)`：授权与收回，保留变更人和时间；收回前返回影响范围，缺省收回该账号待收回清单。
- `review(id)`：收回后按账号复核一次，复核状态跨重新审计保留。

运行：`python3 demo.py`；测试：`python3 -m unittest discover -s tests`
