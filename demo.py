"""乱序分片演示：版本取高、空值保护、冲突双侧保留、断点续合与幂等重放。

运行：python3 demo.py
"""

import importlib
import os
import tempfile

from crawlmerge import merge, resume

mm = importlib.import_module("crawlmerge.merge")

state_dir = os.path.join(tempfile.gettempdir(), "crawlmerge_demo")
batch = "demo"
mm._reset()

# 乱序到达的带号分片：分片号 3 先到、1 后到；同主键跨版本、含空值与同版本冲突
shards = [
    {"shard": 3, "rows": [
        {"id": "p1", "version": 1, "name": "先到的名字", "price": None},
    ]},
    {"shard": 2, "rows": [
        {"id": "p2", "version": 1, "name": "A", "price": 10},
        {"id": "p2", "version": 1, "name": "B", "price": 10},
    ]},
    {"shard": 1, "rows": [
        {"id": "p1", "version": 3, "name": None, "price": 99},
        {"id": "p3", "version": 1, "name": "new", "price": None},
    ]},
]

first = merge(shards, batch=batch, state_dir=state_dir)
print("首次合并:", {"rows": first["rows"], "processed": first["processed"],
                "conflicts": len(first["conflicts"])})
print("断点:", first["token"])

# 失败续合：模拟失败后，带着 token 从断点继续
progress = resume(first["token"])
print("断点进度:", {"done": progress["done"], "rows": progress["rows"]})

# 幂等重放：同一批分片再交一次，已合并位置直接跳过
again = merge(shards, batch=batch, state_dir=state_dir)
print("重放结果:", {"rows": again["rows"], "processed": again["processed"],
                "conflicts": len(again["conflicts"])})

print("清单(按分片号):", [(m["shard"], m["position"], m["rows"])
                          for m in again["checkpoint"]["manifest"]])
print("冲突(按主键聚合):", [(c["id"], [s["row"]["name"] for s in c["sides"]])
                          for c in again["conflicts"]])
print("p1 最终行(版本取高+空值保护):", mm._rows["p1"])
print("p3:", mm._rows["p3"])
