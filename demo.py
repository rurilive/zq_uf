from iocnorm import cleanup, export, match, normalize, restore, set_confidence

FEEDS = [
    {"source": "feed-a", "value": "1.2.3.4", "expires": 8, "confidence": 60},
    {"source": "feed-b", "value": "1.2.3.4", "expires": 17, "confidence": 90},
    {"source": "feed-c", "value": "Evil.Example.COM.", "expires": 5, "confidence": 40},
]

print(normalize(FEEDS, 11))
print(match("1.2.3.4", 11))
print(export(11))

# 第二段演示：过期清理与恢复、来源可信度人工调整
print(cleanup(11))
print(export(11))
print(restore())
print(set_confidence("feed-c", 95))
print(match("evil.example.com"))
