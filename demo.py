from auditquery import configure, load, page, search

# 一段历史审计数据
events = [{"ts": 1_700_000_000 + i * 60,
           "user": ("etl", "bi", "app")[i % 3],
           "op": ("select", "drop")[i % 2]} for i in range(10_000)]
load(events)

# 按时间范围 + 操作人过滤，时间倒序，流式取第一页
res = search({"user": "etl", "start": 1_700_010_000, "end": 1_700_400_000})
print("total:", res["total"], "truncated:", res["truncated"])
for hit in res["hits"][:3]:
    print(hit)

# 沿游标翻页，游标带查询条件和位置标记
cur = res["cursor"]
while cur:
    res = page(cur)
    if res["stale"]:
        print("数据已变化，以上结果为旧快照位置之后的内容")
    cur = res["cursor"]

# 索引不可用时退回扫描，上限可配
configure(use_index=False, max_hits=50_000)
print("scan path:", search({"op": "drop"})["index"])  # False = 扫描路径
