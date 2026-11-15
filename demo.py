"""failclass 演示：分类 + 站点级预算 + 冷却 + 复核清单。"""

from datetime import datetime, timedelta, timezone

from failclass import FailureRouter, classify, policy

print("--- 分类 ---")
print(classify("timeout"))
print(classify("", {"status": 403, "headers": {"Retry-After": "120"}}))
print(classify("", {"status": 404}))
print(policy("blocked"))

now = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
clock = lambda: now  # noqa: E731
router = FailureRouter(budget=3, blocked_cooldown=300, clock=clock)
url = "https://shop.example.com/item"

print("\n--- 超时：等一会儿再试，预算按站点共享 ---")
for task in ("task-1", "task-2"):
    r = router.handle(task, TimeoutError("read timed out"), url=url)
    print(task, r["reason"], r["action"], "delay=", r["delay"], "remaining=", r["remaining"])

print("\n--- 封禁：换出口并冷却，期间同站点请求只需等待 ---")
r = router.handle("task-3", "", {"status": 403, "url": url}, request_id="req-task-3")
print("task-3", r["reason"], r["action"], "cooldown=", r["cooldown"])
print("task-4 gate ->", router.gate("shop.example.com", task="task-4")["action"])

print("\n--- 页面不存在：直接放弃，不消耗预算，进复核 ---")
r = router.handle("task-5", "", {"status": 404, "url": url + "/gone"})
print("task-5", r["reason"], r["action"], "review=", r["review"],
      "remaining=", r["remaining"])

print("\n--- 按站点查看失败构成 ---")
for site, info in router.summary().items():
    print(site, info["counts"], "cooling=", info["cooling"], "review=", info["review"])

print("\n--- 可追溯到具体请求 ---")
for entry in router.history(site="shop.example.com", reason="blocked"):
    print(entry["request_id"], entry["action"], "status=", entry["evidence"]["status"])

print("\n--- 复核清单按站点导出 ---")
print(router.export_review(site="shop.example.com", fmt="csv"))

print("--- 冷却结束后自动恢复，无需改配置 ---")
now += timedelta(seconds=301)
print("gate:", router.gate("shop.example.com"))

print("\n--- 复核后重新入队，不计费 ---")
router.requeue("task-5")
r = router.handle("task-5", "boom", url=url + "/gone")
print(r["action"], "charged=", r["charged"], "remaining=", r["remaining"])
