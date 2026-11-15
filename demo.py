"""演示：站点改版前后，记忆的命中、失效与重探。"""

from hostmemo import events, get, remember, report_features, stats

SITE = "shop"

# 第一段：首次探测，把请求头、编码和成功特征写进记忆，之后直接命中
remember(SITE, {
    "headers": {"User-Agent": "demo/1.0"},
    "encoding": "gbk",
    "features": {"title": "旧版商城", "charset": "gbk"},
})
print("首次命中:", get(SITE)["cached"])
print("复用画像:", get(SITE)["profile"])

# 第二段：站点改版，成功特征变化 -> 记忆失效，触发重探
report_features(SITE, {"title": "新版商城", "charset": "utf-8"})
result = get(SITE)
print("改版后命中:", result["cached"], "需要重探:", result["probe"])

# 重探拿到新画像，写回记忆后恢复命中
remember(SITE, {
    "headers": {"User-Agent": "demo/2.0"},
    "encoding": "utf-8",
    "features": {"title": "新版商城", "charset": "utf-8"},
})
print("重探后命中:", get(SITE)["cached"])

print("事件记录:", [(e["event"], e.get("reason")) for e in events(SITE)])
print("站点统计:", stats(SITE))
