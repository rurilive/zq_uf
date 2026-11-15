"""大站批量入队 vs 小站：配额、老化提权、插队上限、保底与超时降级演示。"""

import crawlprio2 as q

clock = [1000.0]  # 可手动推进的时钟，模拟等待

q.configure(
    site_quota=2,            # 单站最多连续出队 2 个
    tenant_quota=3,          # 单租户最多连续出队 3 个
    aging_rate=0.5,          # 每等 1 秒提权 0.5
    aging_cap=3,             # 提权最多 +3
    preempt_threshold=8,     # 有效优先级 >= 8 才允许插队
    preempt_quota=1,         # 每站每 30 秒只许插队 1 次
    preempt_window=30,
    guarantee_window=4,      # 每 4 次出队给低优保底 1 次
    low_priority=2,
    max_wait=15,             # 等超 15 秒进降级队列
    timeout_action="degrade",
    clock=lambda: clock[0],
)

# 大站批量入队（同一租户）
for i in range(6):
    q.push({"id": "big-%d" % i, "site": "big.example", "tenant": "news-corp", "priority": 5})
# 小站低优任务
q.push({"id": "small-0", "site": "tiny.example", "tenant": "blog", "priority": 1})
q.push({"id": "small-1", "site": "tiny.example", "tenant": "blog", "priority": 1})

clock[0] += 8  # 队列等了一会儿，老任务开始提权

# 大站两个高优任务：第一个能插队，第二个受站点插队配额限制
q.push({"id": "urgent-0", "site": "big.example", "tenant": "news-corp", "priority": 9})
q.push({"id": "urgent-1", "site": "big.example", "tenant": "news-corp", "priority": 9})

while True:
    item = q.pop()
    if item is None:
        break
    d = item["dequeue"]
    print("%-9s pri=%d site=%-12s reason=%-14s wait=%4.1fs boost=%.1f"
          % (item["id"], item["priority"], item["site"],
             d["reason"], d["wait"], d["boost"]))
    clock[0] += 2  # 每出队一次过去 2 秒

print()
for key, value in q.stats().items():
    print("%s: %s" % (key, value))
