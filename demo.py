import os
import tempfile

from partitionstats import hotspots, invalidate, query, stats

root = tempfile.mkdtemp()
# 跨月分区：1 月小、2 月大、3 月中等，行内带 ts
for month, n in (("2024-01", 5), ("2024-02", 50), ("2024-03", 20)):
    with open(os.path.join(root, "%s.jsonl" % month), "w") as fh:
        for i in range(n):
            fh.write('{"ts": "%s-%02dT00:00:00", "v": %d}\n' % (month, i % 28 + 1, i))

print("统计:", stats(root))

result = query(root, {"start": "2024-02-01", "end": "2024-02-29"})
print("扫描:", result["scanned"])
print("跳过:", result["skipped"])
print("耗时: %.6fs" % result["elapsed"])

print("热点:", [(h["partition"], h["hits"], h["size"]) for h in hotspots(root)])

# 写入新分区后失效，统计随之更新
with open(os.path.join(root, "2024-04.jsonl"), "w") as fh:
    fh.write('{"ts": "2024-04-01T00:00:00"}\n')
invalidate(root)
print("写入后分区数:", stats(root)["files"])
