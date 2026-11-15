from sitehealth import report, score

print(report("shop", {"ok": True, "rtt": 0.2}))
print(report("shop", {"ok": False, "status": 429}))
print(score("shop"))

# TODO: 第二段演示（实现后补）
