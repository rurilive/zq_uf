from formcaptcha import (
    AccountBusyError,
    expired_records,
    queue_snapshot,
    resolve,
    resolve_records,
    submit,
)
from formcaptcha import ticket

# 可控时钟，演示确定性超时
now = [1000.0]
ticket._clock = lambda: now[0]

# 高优插队：T-3 排到队首
submit("acc-1", "login")
submit("acc-2", "pay")
submit("acc-3", "risk-control", priority=9)
print("队列(高优插队):", [(t["id"], t["account"], t["priority"]) for t in queue_snapshot()])

# 账号互斥：acc-1 有进行中工单，再提报错
try:
    submit("acc-1", "again")
except AccountBusyError as e:
    print("账号互斥:", e)

# 回写确认：T-3 确认后出队，重复回写报错
print("回写:", resolve("T-3", "8f3k"))

# 超时回收：时钟越过 deadline，acc-1/acc-2 的工单被回收并释放账号
now[0] += 120
submit("acc-1", "retry")
print("超时回收:", expired_records())
print("回写记录:", resolve_records())
print("当前队列:", [t["id"] for t in queue_snapshot()])
