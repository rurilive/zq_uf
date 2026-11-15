"""演示：同一页面两次改版，广告/计数器噪声不报警，变更定位到块。"""
import tempfile
import time

from snapdiff import SnapshotStore, diff, snapshot

BASE = """
<html><body>
<header><h1>每日要闻</h1></header>
<div class="ad-banner">广告位A：春季促销</div>
<p>第一条：今日要闻正文。</p>
<p>第二条：天气预报，晴。</p>
<div class="counter">访问次数 100</div>
<script>track(1)</script>
</body></html>
"""

# 改版一：正文第二段改写；广告轮换、计数器+1、脚本版本变化（均为噪声）
REV1 = BASE.replace("天气预报，晴", "天气预报，晴转多云，注意出行") \
           .replace("广告位A：春季促销", "广告位B：夏季清仓") \
           .replace("访问次数 100", "访问次数 101") \
           .replace("track(1)", "track(2)")

# 改版二：新增一条新闻，删除第一条
REV2 = REV1.replace("<p>第一条：今日要闻正文。</p>", "") \
           .replace("</body>", "<p>第三条：晚间快讯上线。</p></body>")

store = SnapshotStore(tempfile.mkdtemp(prefix="snapdiff-demo-"), retention_days=7)
site = "news.example"
t0 = time.time()

v1 = snapshot({"site": site, "html": BASE, "ts": t0}, store=store)
v2 = snapshot({"site": site, "html": REV1, "ts": t0 + 3600}, store=store)
v3 = snapshot({"site": site, "html": REV2, "ts": t0 + 7200}, store=store)

r1 = diff(v1, v2, store=store)
print("改版一:", r1["summary"])
for b in r1["blocks"]:
    print("  [%s] %s\n    前: %s\n    后: %s" % (b["id"], b["change"], b["before"], b["after"]))

r2 = diff(v2, v3, store=store)
print("改版二:", r2["summary"])
for b in r2["blocks"]:
    print("  [%s] %s\n    前: %s\n    后: %s" % (b["id"], b["change"], b["before"], b["after"]))

print("版本列表:", store.versions(site))
print("按时间取 t0+4000 -> 版本", store.get_at(site, t0 + 4000)["version"])
print("变更查询(站点+时间):", [(c["from_version"], c["to_version"], c["summary"])
                               for c in store.query_changes(site, start=t0, end=t0 + 7200)])
print("清理过期快照:", store.cleanup(now=t0 + 8 * 86400), "个；剩余版本:", store.versions(site))
