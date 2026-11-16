from iocnorm import export, match, normalize

FEEDS = [
    {"source": "feed-a", "value": "1.2.3.4", "expires": 8, "confidence": 60},
    {"source": "feed-b", "value": "1.2.3.4", "expires": 17, "confidence": 90},
    {"source": "feed-c", "value": "Evil.Example.COM.", "expires": 5, "confidence": 40},
]

print(normalize(FEEDS))
print(match("1.2.3.4", 11))
print(export(11))

# TODO: 第二段演示（实现后补）
