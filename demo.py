"""演示：分类 + 站点预算 + 复核队列。"""

import tempfile

from failclass import FailureGovernor, classify, policy

print("1. 失败归一")
print("  timeout   ->", classify("timeout")["reason"])
print("  TimeoutError ->", classify(TimeoutError("read timed out"))["reason"])
print("  403       ->", classify("HTTP 403 banned")["reason"])
print("  status=404 ->", classify(None, {"status": 404})["reason"])
print("  随机异常   ->", classify(ValueError("boom"))["reason"])

print("\n2. 每种原因的策略")
for reason in ("timeout", "blocked", "not_found", "error"):
    rule = policy(reason)
    print(f"  {reason:9s} action={rule['action']:13s} delay={rule['delay']}s "
          f"switch_egress={rule['switch_egress']} cooldown={rule['cooldown']}s")

print("\n3. 同站点共享预算（额度耗尽后转复核，不会每任务各给一份）")
gov = FailureGovernor(max_retries_per_site=3, blocked_cooldown=60)
for i in range(1, 5):
    d = gov.record_failure(
        "shop.example", TimeoutError("read timed out"),
        {"status": 504, "headers": {"X-Request-Id": f"req-{i}"}},
        task_id=f"t{i}", request_id=f"req-{i}",
    )
    print(f"  t{i}: reason={d['reason']} action={d['action']} "
          f"remaining={d['remaining']} status={d['evidence']['status']}")

print("\n  封禁（独立站点）：换出口 + 冷却，冷却期内其它失败只能等")
blocked_gov = FailureGovernor(max_retries_per_site=3, blocked_cooldown=60)
d = blocked_gov.record_failure("api.example", "HTTP 429 rate limit",
                              {"status": 429, "headers": {"Retry-After": "60"}},
                              task_id="t9", request_id="req-9")
print(f"  blocked: action={d['action']} switch_egress={d['switch_egress']} "
      f"cooldown_until={d['cooldown_until']}")
d = blocked_gov.record_failure("api.example", "timeout", task_id="t10")
print(f"  冷却期内 timeout: action={d['action']} delay={round(d['delay'], 1)}s")

print("\n4. 按站点查看失败构成")
print("  ", gov.failure_breakdown("shop.example"))

print("\n5. 复核清单 + 导出 + 重新入队")
with tempfile.TemporaryDirectory() as tmp:
    print("  review:", [(r["task_id"], r["reason"]) for r in gov.review_tasks("shop.example")])
    out = gov.export_review(f"{tmp}/shop.example.review.json", site="shop.example")
    print("  export:", out["count"], "条 ->", out["path"])
    r = gov.requeue("t4", site="shop.example")
    print("  requeue t4: counted =", r["counted"], "，remaining 仍为",
          gov.site_status("shop.example")["retries_remaining"])

print("\n6. 冷却到点自动恢复")
frozen = {"t": 1000.0}
gov2 = FailureGovernor(max_retries_per_site=5, blocked_cooldown=30,
                       clock=lambda: frozen["t"])
gov2.record_failure("a.test", "429 too many requests", {"status": 429})
print("  冷却中:", gov2.site_status("a.test")["available"])
frozen["t"] += 31
print("  30s 后:", gov2.site_status("a.test")["available"])
