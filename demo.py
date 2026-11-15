"""大批审计日志上的流式检索与分页演示。"""
import random
import time

from auditsearch import configure, export, load, page, search

random.seed(20261115)

N = 300000
USERS = ["etl", "bi", "ops", "admin", "svc"]
TYPES = ["select", "insert", "update", "drop", "grant"]
BASE = 1700000000

events = ({
    "time": BASE + i * 7 + random.randint(0, 6),
    "user": random.choice(USERS),
    "type": random.choice(TYPES),
    "detail": "x" * 20,
} for i in range(N))

t0 = time.monotonic()
print("load:", load(events), "耗时 %.2fs" % (time.monotonic() - t0))

configure(page_size=5)
query = {"user": "etl", "type": "drop",
         "time_from": BASE + N * 3, "time_to": BASE + N * 4}

t0 = time.monotonic()
first = search(query)
print("首页 %d 条 truncated=%s index_used=%s 耗时 %.3fs"
      % (len(first["hits"]), first["truncated"],
         first["page"]["index_used"], time.monotonic() - t0))

seen = [h["time"] for h in first["hits"]]
cursor, pages = first["cursor"], 1
while cursor and pages < 4:
    res = page(cursor)
    seen += [h["time"] for h in res["hits"]]
    cursor, pages = res["cursor"], pages + 1
assert seen == sorted(seen) and len(seen) == len(set(seen)), "翻页重复或乱序"
print("翻 %d 页共 %d 条，时间有序、无重复无遗漏" % (pages, len(seen)))

t0 = time.monotonic()
total = sum(1 for _ in export(query, fields=["time"]))
print("export 同查询导出 %d 条，耗时 %.2fs" % (total, time.monotonic() - t0))

res = search({"user": "admin"}, max_hits=3, fields=["time", "user"])
print("截断+裁剪：%d 条 truncated=%s 字段=%s"
      % (len(res["hits"]), res["truncated"], sorted(res["hits"][0])))
