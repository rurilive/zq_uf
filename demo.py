"""演示：分片清单写入 -> 模拟清单/产物损坏 -> 校验 -> 差异恢复。"""
import json
import os
import tempfile

from crawlmanifest import recover, verify, write

root = tempfile.mkdtemp(prefix="crawl-")


def fetch(shard):
    """模拟重新采集一个分片，返回清单字段。"""
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    rel = os.path.join("data", "shard-%04d.jsonl" % shard)
    with open(os.path.join(root, rel), "w", encoding="utf-8") as handle:
        for i in range(3):
            handle.write(json.dumps({"shard": shard, "i": i}) + "\n")
    return {"artifact": rel}


# 1. 三个分片并发安全地各写各的清单
for shard in range(3):
    write({"shard": shard, **fetch(shard)}, root)
print("写入后校验:", verify(root))

# 2. 模拟损坏：分片 1 清单写一半崩溃，分片 2 产物被改，分片 0 产物丢失
with open(os.path.join(root, "manifests", "manifest-shard-0001.json"), "w") as h:
    h.write('{"shard": 1, "art')
with open(os.path.join(root, "data", "shard-0002.jsonl"), "a") as h:
    h.write("garbage\n")
os.unlink(os.path.join(root, "data", "shard-0000.jsonl"))
report = verify(root)
print("损坏后校验:", report)

# 3. 恢复：recover 内部先跑一次校验，只补缺失/损坏的分片
result = recover(root, fetch)
print("修复分片:", result["repaired"])
print("恢复后校验:", result["report"])
