"""snapdiff 演示：整页哈希 → 段落级比对，以及版本保留。"""
from datetime import datetime, timedelta, timezone

from snapdiff import SnapshotStore, diff, snapshot

PAGE = """<html><head><title>示例</title></head><body>
<h1>站点标题</h1>
<p>第一段正文。</p>
<div class="ad-slot"><p>今日广告</p></div>
<p>浏览量 123 次</p>
<script>var built = 1</script>
</body></html>"""

site = "https://example.com/a"
day0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
store = SnapshotStore(clock=lambda: (day0 + timedelta(days=0)).timestamp())


def take(html, day):
    return snapshot({"html": html, "url": site,
                     "timestamp": day0 + timedelta(days=day)}, store=store)


# 第一段演示：脚本、广告、计数器变化都不算改版。
old = take(PAGE, 0)
noise = take(PAGE.replace("今日广告", "明日广告")
                 .replace("123 次", "456 次")
                 .replace("var built = 1", "var built = 2"), 1)
print("广告/脚本/计数器变化:", diff(old, noise)["summary"],
      "| alert =", diff(old, noise)["alert"])

# 真正改正文：定位到段落，给出前后摘要和版本号。
changed = take(PAGE.replace("第一段正文。", "第一段正文改写了。"), 2)
result = diff(old, changed, store=store)
print("正文变化:", result["summary"])
print("变化段落:", result["paragraphs"], "| 旧版", result["old_version"], "→ 新版", result["version"])

# 告警关闭后仍保留比对结果。
store.disable_alerts(site)
print("关闭告警后 alert =", diff(changed, take(PAGE.replace("第一段正文。", "第一段又改了。"), 3),
                                 store=store)["alert"],
      "| 仍存结果 =", len(store.results(site)))

# 版本按时间取回，清理至少保留两版。
print("历史:", [(v["version"], v["created_at"][:10]) for v in store.history(site)])
print("清理删除:", store.prune(site), "| 剩余:", [v["version"] for v in store.history(site)])
