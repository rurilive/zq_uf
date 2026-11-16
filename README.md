# honeypot
安全方向的小工具，用来核对账号权限和实际使用情况。
audit(account) 比对授权范围与 used，返回未使用、长期未使用（stale）、异常账号（left/transferred）和待收回清单。
report(system=...) 按系统汇总，待收回清单按系统和岗位分组，另含长期未登录统计和待复核账号。
grant/reclaim 记录变更人和时间，changes() 按时间排序；reclaim(dry_run=True) 先看影响范围。
recheck(account_id) 在收回后按账号复核一次，未变化的账号不进入待复核。
运行：python3 demo.py；测试：python3 -m unittest tests.test_audit
