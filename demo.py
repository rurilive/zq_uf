from linksieve import LinkStore, due, fingerprint

print(fingerprint("https://shop/p?utm_source=x"))
print(due("https://shop/p", 100))
print(due("https://shop/p", 200))

# 同一篇内容的不同写法：追踪参数、参数顺序、页尾锚点都应指向同一个目标。
store = LinkStore()
print(store.due("https://shop/p?id=7&utm_source=mail", 100))   # 新目标
print(store.due("https://shop/p?id=7&utm_medium=web", 200))    # 别名，不给名额
print(store.due("https://shop/p?id=7#comments", 300))          # 别名
print(store.due("https://shop/p?id=7&page=2", 300))            # 分页是另一个目标

print(store.alias_report("shop"))
print(store.alerts("shop"))
print(store.daily_metrics("shop"))

# 热点页更早回来。
for step in range(3):
    now = 400 + step * 10
    store.due("https://shop/hot", now)
    store.observe("https://shop/hot", now, changed=True)
print(store.due("https://shop/hot", 430)["matched_rule"])

# 规则更新后，历史链接重新判定。
rules_diff = store.rejudge()
print(rules_diff["merged_samples"])

# 单站点试运行，出问题只回退这个站点。
store.start_trial("trial.shop")
print(store.due("https://trial.shop/a?utm_source=x", 100))
print(store.due("https://trial.shop/a#top", 200))
print(store.site_alerts("trial.shop"))
store.rollback("trial.shop")
