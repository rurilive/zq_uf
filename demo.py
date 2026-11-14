from blockvote import allow, observe, probes, status, transitions, whitelist

# 误判场景：一次403只进观察态，探针连续成功后自动恢复
observe("shop", "403", now=0.0)
print("allowed", allow("shop"))            # False，观察态
print("status", status("shop"))
observe("shop", "200", now=5.0)            # 探针1
observe("shop", "200", now=5.5)            # 间隔不足，被忽略
observe("shop", "200", now=7.0)            # 探针2，解除
print("allowed", allow("shop"))            # True

# 真实封禁：多信号投票超过熔断阈值
observe("api", "captcha", now=0.0)
observe("api", "403", now=0.0)             # 5票，熔断
print("status", status("api"))

# 人工加白：期内信号不计票，过期后恢复计票
whitelist("cdn", ttl=30, now=0.0)
observe("cdn", "403", now=1.0)
print("allowed", allow("cdn", now=2.0))    # True
observe("cdn", "403", now=31.0)
print("allowed", allow("cdn", now=31.0))   # False

print("transitions")
for t in transitions():
    print(" ", t)
print("probes")
for p in probes():
    print(" ", p)
