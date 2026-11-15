"""演示：站点轮转、等待提权、配额、放弃重入、租户隔离、饥饿告警与记录回放。"""

from crawlprio2 import CrawlQueue


class Clock:
    def __init__(self):
        self.t = 0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


clock = Clock()
q = CrawlQueue(clock=clock, starve_days=3)

print("== 1. 大站 4 条、小站 2 条，一批取 4 条：轮转，小站不被挤没 ==")
for i in range(4):
    q.push({"id": f"big-{i}", "site": "big", "priority": 1})
for i in range(2):
    q.push({"id": f"small-{i}", "site": "small", "priority": 1})
for it in q.pop(batch=4)["items"]:
    print(f"  {it['id']:8s} site={it['site']:5s} 提权后优先级={it['effective_priority']} 已等={it['waited_seconds']}s")

print("== 2. 等待 600s 后低优老任务靠提权（上限 5）超过高优新任务 ==")
clock.advance(600)
q.push({"id": "fresh-high", "site": "big", "priority": 3})
print(" ", q.pop(batch=1)["items"][0])

print("== 3. 站点配额用尽：返回可等待时间，剩余名额=0，条目原样排队（独立租户演示） ==")
q.configure_site("quota-demo", "limited", 2)
for i in range(3):
    q.push({"id": f"limited-{i}", "site": "limited", "priority": 9},
           tenant="quota-demo")
print(" ", q.pop(tenant="quota-demo", batch=2))
print(" ", q.pop(tenant="quota-demo", batch=2))  # 全被配额挡住，给出到次日 0 点的等待秒数

print("== 4. 放弃的条目重新入队，保留等待资历，attempt+1（独立租户演示） ==")
q.push({"id": "will-retry", "site": "small", "priority": 2},
       tenant="retry-demo")
q.pop(tenant="retry-demo", batch=1)
print(" ", q.release("will-retry", tenant="retry-demo"))
clock.advance(120)
print(" ", q.pop(tenant="retry-demo", batch=1)["items"][0])

print("== 5. 租户隔离：t2 看不到 t1 的条目，配额互不挤占 ==")
q.push({"id": "t1-task", "site": "big"}, tenant="t1")
print("  t2 pop:", q.pop(tenant="t2")["items"])
print("  t1 pop:", [it["id"] for it in q.pop(tenant="t1")["items"]])

print("== 6. 小站配额为 0，连续 3 天无出队 => 饥饿告警 ==")
hungry = CrawlQueue(clock=clock, starve_days=3)
hungry.configure_site("default", "small", 0)
for day in range(4):
    hungry.push({"id": f"hungry-{day}", "site": "small"})
    hungry.push({"id": f"feed-{day}", "site": "big"})
    alerts = hungry.pop(batch=10)["starvation_alerts"]
    print(f"  day{day} 告警: {[a['site'] for a in alerts]}")
    clock.advance(86400)
print("  告警详情:", hungry.starvation_alerts()[0]["message"])

print("== 7. 出队记录按天/站点查询、导出 CSV、回放 ==")
print(hungry.export_records(day=3, fmt="csv").strip())
for row in hungry.records(day=3):
    hungry.ack(row["id"])
print("  回放 day3:", hungry.replay_day(3))
clock.advance(86400)
print("  回放条目次日重新出队:", [it["id"] for it in hungry.pop(batch=5)["items"]])
