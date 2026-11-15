"""seedscore 演示：打分、入队过滤、影子对比、人工恢复与导出。"""

import json

from seedscore import ReviewLog, filter, score, shadow
from seedscore.export import export_changes, export_report

SEEDS = [
    {"url": "https://news.example.org/", "source": "sitemap",
     "history": {"successes": 40, "failures": 2, "last_success": "2026-11-01T00:00:00Z"}},
    {"url": "https://shop.example.com/", "source": "manual"},
    {"url": "https://www.shop.example.com/?utm_source=newsletter", "source": "crawl"},
    {"url": "https://example.net/login/cart/1/2/3/4/5", "source": "referral"},
    {"url": "https://old.example.com/", "status": "dead"},
    {"url": "https://gone.example.com/", "http_status": 404},
    {"url": "https://new.example.org/", "source": "link", "is_new": True},
]

# 1. 单条打分：给出分数构成，而不是只给一个总分。
one = score(SEEDS[0])
print("== 打分 ==")
print(json.dumps({k: one[k] for k in ("url", "score", "parts", "threshold", "notes")},
                 ensure_ascii=False, indent=1))

# 2. 入队：低于阈值剔除、同目标留最高分、失效单独列出。
report = filter(SEEDS)
print("\n== 入队结果 ==")
print("保留:", [item["url"] for item in report["kept"]])
print("剔除:", [(item["url"], item["reason"]) for item in report["dropped"]])
print("失效:", [(item["url"], item["dead_reasons"]) for item in report["dead"]])
print("汇总:", json.dumps(report["summary"], ensure_ascii=False))

# 3. 重算不改变已入队顺序；改动可按站点查看。
queue = [item["target"] for item in report["kept"]]
rescored = filter(SEEDS, queue=queue, previous=report)
print("\n== 重算 ==")
print("顺序不变:", [item["target"] for item in rescored["kept"]] == queue)
print("变化:", json.dumps(rescored["changes"]["counts"], ensure_ascii=False))

# 4. 影子运行：与人工挑选对比，未覆盖的新站点会阻止启用。
shadow_report = shadow(SEEDS, human=[item["url"] for item in report["kept"][:2]],
                       sample_size=10, seed=1)
print("\n== 影子 ==")
print(shadow_report["verdict"], "-", shadow_report["verdict_reason"])
print("误杀:", [(item["target"], item["score"]) for item in shadow_report["false_kills"]])

# 5. 人工恢复被误杀的种子，并可导出、查询、回放。
review = ReviewLog()
killed = [item for item in report["dropped"] if item["reason"] == "low_score"]
if killed:
    review.restore(killed[0]["url"], actor="alice", reason="新站点，人工确认有内容")
    restored = filter(SEEDS, review=review)
    print("\n== 恢复 ==")
    print("恢复后入选:", [item["url"] for item in restored["kept"] if item.get("restored")])
    print("恢复记录:", json.dumps(review.entries, ensure_ascii=False))

# 6. 导出：保留/剔除清单与评分变化都可用于审计。
print("\n== 导出 ==")
print("报告行数:", len(export_report(report).strip().splitlines()))
print("变化行数:", len(export_changes(rescored["changes"]).strip().splitlines()))
