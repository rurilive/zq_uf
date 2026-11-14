"""演示两次整站抓取的结构指纹比对。"""

import json

from sitemapdiff import Tracker, snapshot


def crawl(site):
    """模拟一次整站抓取结果。"""
    return {
        "links": ["https://%s/%s" % (site["host"], path) for path in site["paths"]],
        "depth": site.get("depth", 1),
    }


def show(title, result):
    print("== %s ==" % title)
    print("指纹 %s -> %s（%s）" % (
        result["fingerprint"]["old"],
        result["fingerprint"]["new"],
        "有变化" if result["fingerprint"]["changed"] else "无变化",
    ))
    summary = result["summary"]
    print("链接 %d -> %d，新增 %d，删除 %d" % (
        summary["links"]["old"], summary["links"]["new"],
        summary["added"], summary["removed"],
    ))
    for section, change in result["changes"]["by_section"].items():
        old = (change["old"] or {}).get("links", 0)
        new = (change["new"] or {}).get("links", 0)
        print("  栏目 %-12s 链接 %d -> %d，新增 %d，删除 %d"
              % (section, old, new, change["added"], change["removed"]))
        if change["samples"]["added"]:
            print("    新增样本: %s" % ", ".join(change["samples"]["added"]))
        if change["samples"]["removed"]:
            print("    删除样本: %s" % ", ".join(change["samples"]["removed"]))
    for alert in result["alerts"]:
        print("  [%s/%s] %s" % (alert["severity"], alert["type"], alert["message"]))
        if alert["samples"]["old"] or alert["samples"]["new"]:
            print("    旧: %s" % (", ".join(alert["samples"]["old"]) or "-"))
            print("    新: %s" % (", ".join(alert["samples"]["new"]) or "-"))
    print()


# 第一次抓取：三栏目，博客文章在 /blog 下，news 有 12 条。
before = snapshot(crawl({
    "host": "example.test",
    "depth": 2,
    "paths": (
        ["blog/post-1", "blog/post-2", "blog/post-3"]
        + ["news/2024-%d" % i for i in range(1, 13)]
        + ["about"]
    ),
}))

# 第二次抓取：博客整体下沉到 /news/blog，新增 shop 栏目，news 只剩 2 条。
after = snapshot(crawl({
    "host": "example.test",
    "depth": 2,
    "paths": (
        ["news/blog/post-1", "news/blog/post-2", "news/blog/post-3"]
        + ["news/2024-1", "news/2024-2"]
        + ["shop/item-%d" % i for i in range(1, 5)]
        + ["about"]
    ),
}))

show("第一段：全量重抓后的结构变更", Tracker({"escalate_after": 2}).diff(before, after))

# 第二段：连续三批都是同样的结构差异，第 2 批起告警升级。
tracker = Tracker({"escalate_after": 2})
for batch in range(1, 4):
    result = tracker.diff(before, after)
    show("第二段：连续第 %d 批（升级阈值 2）" % batch, result)

print("升级告警:", json.dumps(
    [{"key": a["key"], "streak": a["streak"], "severity": a["severity"]}
     for a in result["escalated"]],
    ensure_ascii=False,
))
