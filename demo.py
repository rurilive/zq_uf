"""演示：高优工单插队分派，超时回收后转派并保留历史。"""

from capticket import dispatch, reassign, resolve, status, submit, sweep

NOW = 1_000_000.0  # 固定时钟，便于复现

# 三个低优工单先进队列
for i in range(1, 4):
    submit("acc-%d" % i, {"url": "https://shop/login", "priority": 1, "timeout": 60}, now=NOW)
# 紧急工单后到，优先级 9
submit("acc-vip", {"url": "https://shop/pay", "priority": 9, "timeout": 60}, now=NOW)
print("队列(高优在前):", [q["account"] for q in status(now=NOW)["queue"]])

top = dispatch(now=NOW)  # 高优插队，先派 acc-vip
print("分派:", top["account"], "priority=", top["priority"])
print("回写:", resolve(top["ticket"], "1234", now=NOW))

# 超时回收 + 转派（保留原工单历史）
stale = submit("acc-old", {"url": "https://shop/cart", "priority": 5,
                           "timeout": 5, "cooldown": 0}, now=NOW)
print("超时回收:", [e["ticket"] for e in sweep(now=NOW + 10)])
new = reassign(stale["ticket"], now=NOW + 10)
print("转派: #%d -> #%d, 历史事件: %s"
      % (new["parent"], new["ticket"], [h["event"] for h in new["history"]]))
print("转派回写(resume=True):", resolve(new["ticket"], "9876", now=NOW + 11))
print("回写日志:", status(now=NOW + 11)["writebacks"])
