from linksieve import (RuleSet, alias_alerts, daily_metrics, due, explain,
                       fingerprint, pilot_rules, record_fetch, reset,
                       rollback_rules)

T0 = 1_700_000_000

print(fingerprint("https://shop/p?utm_source=x"))
print(due("https://shop/p", 100))
print(due("https://shop/p", 200))

reset()

# 第二段：归一化判重、别名合并、自适应重访、按站点灰度
print("\n-- 同一篇文章的多种写法只算一条 --")
print(explain("https://news.com/a?id=7&utm_source=app&gclid=9#p3"))
for u in ["https://news.com/a?id=7&utm_source=app",
          "https://news.com/a?id=7&gclid=9#p3",
          "https://news.com/a?id=7"]:
    print(due(u, T0))

print("\n-- 常变页面更早回来 --")
due("https://news.com/hot", T0)
print(record_fetch("https://news.com/hot", T0, changed=True))
print(due("https://news.com/hot", T0 + 900))

print("\n-- 新规则先在 pilot.com 试运行，可只回退该站 --")
pilot_rules("pilot.com", RuleSet(version="v2", tracking_params=frozenset({"sid"})))
print(explain("https://pilot.com/l?sid=42&page=2"))
for i in range(12):
    due("https://pilot.com/p?utm_source=s%d" % i, T0 + i)
record_fetch("https://pilot.com/p?utm_source=s1", T0 + 100, changed=False)
print(daily_metrics("pilot.com"))
print(alias_alerts(ratio=0.5, min_aliases=10))
print(rollback_rules("pilot.com"))
