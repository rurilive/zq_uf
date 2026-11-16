from iocnorm import (
    cleaned,
    expiring,
    export,
    match,
    normalize,
    purge,
    restore,
    set_confidence,
)

FEEDS = [
    {"source": "feed-a", "value": "1.2.3.4", "expires": 8, "confidence": 60},
    {"source": "feed-b", "value": "1.2.3.4", "expires": 17, "confidence": 90},
    {"source": "feed-c", "value": "Evil.Example.COM.", "expires": 5, "confidence": 40},
]

print(normalize(FEEDS, day=11))
print(match("1.2.3.4", day=11))
print(export(day=11))

# 第二段：过期清理、恢复与可信度调整
print(expiring(day=0, within=10))
print(purge(day=11))
print(cleaned())
print(restore("evil.example.com"))
print(set_confidence("feed-a", 95))
print(match("1.2.3.4"))
