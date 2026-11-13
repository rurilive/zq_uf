import os
import tempfile

from fingerprint import Fingerprint

BATCH1 = [
    {"url": "http://news.com/a?id=1", "content": "今日两市高开高走，科技股领涨大盘，成交量明显放大，市场情绪持续回暖。"},
    {"url": "http://news.com/b?id=2", "content": "某球队昨晚三比一逆转取胜，终结对手十连胜，主教练赛后盛赞替补奇兵。"},
]
BATCH2 = [
    {"url": "http://NEWS.com/a?id=1&utm_source=push#top", "content": "今日两市高开高走，科技股领涨大盘，成交量明显放大，市场情绪持续回暖。"},
    {"url": "http://news.com/c?id=3", "content": "今日两市高开高走。科技股领涨大盘，成交量明显放大，市场情绪持续回暖！"},
    {"url": "http://blog.com/x", "content": "一篇全新的独立文章，讨论城市公共交通的末班车调度优化方案。"},
]

path = os.path.join(tempfile.gettempdir(), "fingerprint_demo.json")
if os.path.exists(path):
    os.remove(path)

def run(store, batch, tag):
    for record in batch:
        res = store.check(record)
        if res.hit:
            print("%s %s -> 命中[%s] 相似度=%.2f 首次出现=%.0f" % (tag, record["url"], res.kind, res.similarity, res.first_seen))
        else:
            print("%s %s -> 新文章" % (tag, record["url"]))

with Fingerprint(storage_path=path, threshold=0.9, flush_every=1) as store:
    run(store, BATCH1, "第一批")

with Fingerprint(storage_path=path, threshold=0.9) as store:  # 模拟进程重启后加载
    run(store, BATCH2, "第二批")
    for site, s in sorted(store.stats().items()):
        print("站点 %s: 检查=%d 命中=%d 命中率=%.0f%%" % (site, s["checked"], s["hits"], s["hit_rate"] * 100))
