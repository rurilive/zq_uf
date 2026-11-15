"""演示：同一文章多种写法合并、别名告警、按天观测与站点级试运行。"""
from linksieve import LinkStore, due, fingerprint

print("== 同一内容的不同写法 ==")
variants = [
    "https://shop.example/article/42?utm_source=weibo&utm_medium=social",
    "https://shop.example/article/42?utm_medium=social&utm_source=weibo",
    "https://shop.example/article/42?fbclid=abc123",
    "https://shop.example/article/42#comments",
    "https://shop.example/article/42",
]
for url in variants:
    print(fingerprint(url), url)

print("\n== 分页与路径仍算不同目标 ==")
for url in ("https://shop.example/article/42?page=2",
            "https://shop.example/article/43"):
    print(fingerprint(url), url)

print("\n== 对外入口 due(url, now) ==")
print(due("https://shop.example/article/42", 100))
print(due("https://shop.example/article/42?utm_source=x", 200))

print("\n== 一站的别名归并与告警 ==")
store = LinkStore(alert_min_aliases=3, alert_min_ratio=0.5)
now = 1_700_000_000
store.due("https://hot.example/42", now)
for i in range(6):
    store.due(f"https://hot.example/42?utm_source=s{i}", now + 10 + i)
result = store.due("https://hot.example/42?utm_source=last", now + 30)
print("kept:", result["kept_url"], "| is_alias:", result["is_alias"],
      "| aliases:", result["stats"]["aliases"])
print("alert:", store.alias_alerts()[0]["alias_variants"], "个别名")

print("\n== 按天查看别名比例与重复抓取 ==")
for row in store.daily_series("hot.example"):
    print(row)

print("\n== 热点页更早回来 ==")
store.due("https://hot.example/feed", now)
store.observe("https://hot.example/feed", "v1", now)
store.due("https://hot.example/feed", now + 3600)
print(store.observe("https://hot.example/feed", "v2", now + 3600))
store.due("https://hot.example/feed", now + 7200)
print(store.observe("https://hot.example/feed", "v3", now + 7200))

print("\n== 单站试运行与回退 ==")
store.due("https://other.example/1", now)
canary = store.start_canary("hot.example", tracking_prefixes=("utm_", "spm_"))
print("hot ruleset:", canary["rules"].version,
      "| other ruleset:", store.rules_for("other.example").version)
print("rollback:", store.rollback_site("hot.example"))
print("hot ruleset after rollback:", store.rules_for("hot.example").version)
