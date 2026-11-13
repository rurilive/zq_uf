import tempfile
import time

from pagesnap import diff, get, save

NOW = time.time()
DAY = 86400

# 同一页面三次改版（最近三天）
VERSIONS = [
    {"id": "home", "site": "shop", "time": NOW - 2 * DAY,
     "text": "欢迎来到商店首页，今日特价商品已更新。",
     "html": "<html><body><header><h1>商店</h1></header><main><p>特价</p></main></body></html>"},
    {"id": "home", "site": "shop", "time": NOW - DAY,
     "text": "欢迎来到商店首页，今日特价商品已更新，新增会员专享折扣。",
     "html": "<html><body><header><h1>商店</h1></header><main><section><p>特价</p></section></main></body></html>"},
    {"id": "home", "site": "shop", "time": NOW,
     "text": "欢迎来到全新商店，会员专享折扣进行中。",
     "html": "<html><body><header><nav><h1>商店</h1></nav></header><main><section><p>会员</p></section></main><footer>联系我们</footer></body></html>"},
]

with tempfile.TemporaryDirectory() as tmp:
    paths = [save(page, tmp, retention_days=30) for page in VERSIONS]

    print("== 按时间点取回 ==")
    snap = get("home", tmp, site="shop", at=NOW - DAY / 2)
    print(f"半天前的快照: {snap['text']}")

    for i, (a, b) in enumerate(zip(paths, paths[1:]), 1):
        result = diff(a, b)
        print(f"\n== 第 {i} 次改版 ==")
        print(f"文本变化: {result['text_change_percent']}%  结构变化: {result['structure_change_percent']}%  总变化: {result['change_percent']}%")
        print(f"新增节点: {result['added_nodes']}  删除节点: {result['removed_nodes']}")
