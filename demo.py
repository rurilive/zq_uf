"""一组变体链接的归一化与等价类演示。"""

import os

from urlcanon import canon, merge, merge_log, normalize, representative, rule_hits, same

INDEX = "demo-index.json"
if os.path.exists(INDEX):
    os.remove(INDEX)
canon.configure(INDEX)

merge("http://a/x?utm_source=1", "http://a/x")
merge("http://a/x", "http://a/x/")
print("a and c same:", same("http://a/x?utm_source=1", "http://a/x/"))

# 第二段：同一页面的一组变体（端口/大小写/默认首页/追踪参数/参数顺序/fragment）
variants = [
    "HTTP://Example.com:80/index.html?utm_source=ads&b=2&a=1#top",
    "http://example.com/?a=1&b=2",
    "http://example.com/index.html?b=2&a=1&gclid=xyz",
    "http://example.com/?b=2&a=1",
]
for url in variants:
    print(normalize(url), "<-", url)
print("variants same:", all(same(variants[0], v) for v in variants[1:]))
print("representative:", representative(variants[0]))
print("rule hits for first variant:")
for hit in rule_hits(variants[0]):
    print(" ", hit["rule"], hit["priority"], hit["detail"])
print("merge log:")
for record in merge_log():
    print(" ", record)

