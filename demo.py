import logging

from seedexpand import budget, configure, expand, reset, score_link

logging.basicConfig(level=logging.INFO, format="%(message)s")

# 模拟一个三层站点：首页 -> 列表页 -> 详情页，夹带导航和媒体链接。
SITE = {
    "https://shop/": [
        "https://shop/category/1",
        "https://shop/category/2",
        "https://shop/item/1",
        "https://shop/about",
        "https://shop/login",
        "https://shop/logo.png",
        "http://shop/item/1/",  # 协议 + 尾斜杠差异，应判为重复
    ],
    "https://shop/category/1": [
        "https://shop/item/1",
        "https://shop/item/2",
        "https://shop/item/3",
        "https://shop/page/2",
        "https://shop/about",
    ],
    "https://shop/category/2": [
        "https://shop/item/2",
        "https://shop/item/4",
        "https://shop/page/2",
    ],
    "https://shop/item/1": ["https://shop/category/1", "https://shop/about"],
    "https://shop/item/2": ["https://shop/item/5"],
    "https://shop/item/3": ["https://shop/item/6"],
    "https://shop/page/2": ["https://shop/item/7", "https://shop/item/8"],
    "https://shop/item/5": ["https://shop/item/9", "https://shop/logo.png"],
}


def crawl(start, max_depth):
    """从 start 出发逐层扩散，每层只展开上一层新增的目标。"""
    results = []
    frontier = [start]
    for depth in range(max_depth + 1):
        next_frontier = []
        for url in frontier:
            page = {"url": url, "links": SITE.get(url, [])}
            results.append((url, expand(page, depth)))
            next_frontier.extend(results[-1][1]["added"])
        frontier = next_frontier
        if not frontier:
            break
    return results


def show(source, batch):
    print("== 展开来源页 %s（深度 %d → 目标深度 %d）"
          % (source, batch["depth"], batch["depth"] + 1))
    for entry in batch["added_detail"]:
        print(
            "  + %-28s cat=%-7s score=%-3d src=%s"
            % (entry["url"], entry["category"], entry["score"], entry["source"])
        )
    for entry in batch["truncated"]:
        print("  - %-28s cat=%-7s reason=%-12s" % (entry["url"], entry["category"], entry["reason"]))
    for entry in batch["duplicates"]:
        print("  = %-28s reason=%-18s first_source=%s"
              % (entry["url"], entry["reason"], entry["first_source"]))
    print("  新增=%d 截断=%d 重复=%d"
          % (len(batch["added"]), len(batch["truncated"]), len(batch["duplicates"])))


reset()
configure("shop", quotas={1: 4, 2: 3, 3: 1}, max_depth=3, category_caps={"nav": 1})

print("链接优先级示例:",
      [(u, score_link(u)) for u in ("https://shop/item/1", "https://shop/category/1", "https://shop/about")])
print()

for source, batch in crawl("https://shop/", max_depth=3):
    show(source, batch)
    print()

print("== 预算快照")
snapshot = budget("shop")
for depth, layer in snapshot["layers"].items():
    print("  深度 %d: limit=%d used=%d remaining=%d categories=%s"
          % (depth, layer["limit"], layer["used"], layer["remaining"], layer["categories"]))
print("  队列深度=%d 深度分布=%s" % (snapshot["queue_depth"], snapshot["depth_counts"]))
print("  最近入队速率=%.2f/s" % snapshot["enqueue_rate"])
