"""演示: 热点与归档混合下的保底、老化提权和落盘恢复。"""
import os
import tempfile

from urlqueue import pop, push, stats
from urlqueue import queue

queue.configure(state_path=os.path.join(tempfile.mkdtemp(), "demo_state.json"))

# 可快进的时钟, 用于演示老化(真实环境默认 time.time)
now = [0.0]
queue._clock = lambda: now[0]

# 第一段: 归档链接先入场, 热点链接持续插队
push("http://archive/1", 1)
push("http://archive/2", 1)
for i in range(6):
    push("http://hot/%d" % i, 9)

print("== 热点插队, 归档保底 ==")
for _ in range(5):
    item = pop()
    print("  %-20s base=%s score=%.2f" % (item["url"], item["priority"], item["score"]))
print("  统计:", stats())

# 第二段: 时间流逝, 归档链接老化提权, 反超新到的热点链接
now[0] += 600  # 剩余链接等待 10 分钟, 各 +10 分
push("http://hot/new", 9)  # 刚到的热点链接, 无老化加分
print("\n== 10 分钟后, 归档老化提权 ==")
print("  当前分数:", stats()["scores"])
for _ in range(2):
    item = pop()
    print("  出队: %-20s base=%s score=%.2f" % (item["url"], item["priority"], item["score"]))
print("  归档 archive/2 凭老化 +10 分排在新到热点 hot/new(9 分)之前")

# 第三段: 落盘 + 模拟重启恢复
print("\n== 模拟重启 ==")
queue._queue.clear()
print("  恢复条数:", queue.load())
print("  恢复后统计:", stats())
