"""模拟热点新闻和归档任务混合的采集队列。

用假时钟让输出完全确定，和 tests/test_fresh_dynamic.py 的断言一一对应。
"""
from queuefresh import Queue

PARAMS = dict(half_life=60.0, boost_threshold=30.0, boost_step=2.0)


def drain(q):
    while q.size():
        task = q.pop()
        print(task["id"], round(task["score"], 4), task["boosts"])


# 第一段：热点 + 归档混合，按新鲜度衰减排序
now = [10000.0]
q = Queue(clock=lambda: now[0], **PARAMS)
q.push({"id": "hot-1", "priority": 1,
        "published_at": now[0], "deadline": now[0] + 120})
q.push({"id": "archive-1", "priority": 9, "published_at": now[0] - 300})
q.push({"id": "hot-2", "priority": 2,
        "published_at": now[0], "deadline": now[0] + 120})
now[0] += 45  # 等待 45 秒，超过提权阈值 30 秒，各提权 1 次
print("# 热点+归档混合（等待45s后出队）")
drain(q)

# 第二段：归档任务长期等待，提权 7 次后反超新热点，避免饿死
now2 = [20000.0]
q2 = Queue(clock=lambda: now2[0], **PARAMS)
q2.push({"id": "archive-2", "priority": 9, "published_at": now2[0]})
now2[0] += 220  # 等待 220 秒，提权 220 // 30 = 7 次
q2.push({"id": "hot-3", "priority": 1,
         "published_at": now2[0], "deadline": now2[0] + 120})
print("# 防饿死（归档等待220s后新热点入队）")
drain(q2)
