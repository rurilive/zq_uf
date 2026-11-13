import json

from failcluster import record, report

STACK_A = 'Traceback:\n  File "crawl/fetch.py", line 41, in fetch\n  File "crawl/net.py", line 12, in get'
STACK_B = 'Traceback:\n  File "crawl/parse.py", line 8, in extract'

# 网络超时：同一根因散布在 5 个任务，持续 2 小时
for i in range(5):
    record({"site": "shop", "error": "timeout", "selector": "#price",
            "stack": STACK_A, "task": "task-%d" % i, "url": "/item/%d" % i,
            "ts": 1000 + i * 1800})
# 解析失败：偶发两条
record({"site": "news", "error": "parse", "selector": "//article",
        "stack": STACK_B, "task": "task-9", "url": "/a/1", "ts": 1200})
record({"site": "news", "error": "parse", "selector": "//article",
        "stack": STACK_B, "task": "task-9", "url": "/a/2", "ts": 1500})
# 规则失效：先出现，沉寂后复发（回归）
for ts in (2000, 2300, 2500, 20000, 20300):
    record({"site": "blog", "error": "selector_miss", "selector": ".title",
            "task": "task-7", "url": "/post", "ts": ts})

data = report()
print("total:", data["total"])
for c in data["clusters"]:
    print("[%s] %s/%s selector=%s tasks=%d count=%d duration=%ds" % (
        c["category"], c["key"]["site"], c["key"]["error"],
        c["key"]["selector"], c["affected_tasks"], c["count"], c["duration"]))

print("\n-- 最近 1 小时窗口 --")
print(json.dumps(report(since=19000, until=21000)["clusters"],
                 ensure_ascii=False, indent=2))
