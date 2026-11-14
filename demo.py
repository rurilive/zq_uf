"""环形站点演示：归一化建图 + 迭代环路分析 + 截断。"""

from linkgraph import build, cycles

pages = {
    "http://shop.example/": ["http://shop.example/cat"],
    "http://shop.example/cat": ["http://shop.example/item?from=cat"],
    "http://shop.example/item": [
        "http://shop.example/cart",
        "http://shop.example/item?ref=ad",
    ],
    "http://shop.example/cart": ["http://shop.example/"],
    "http://shop.example/help": ["http://shop.example/help#top"],
}

build(pages)
report = cycles(limit=100)
print("nodes", report["nodes"])
print("edges", report["edges"])
for entry in report["cycles"]:
    print("cycle", " -> ".join(entry["path"]), "| nodes:", entry["nodes"])
print("truncated", report["truncated"])

small = cycles(limit=3)
print("limit=3 truncated", small["truncated"])
