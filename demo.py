"""两方并发更新模拟：版本冲突、合并重试、删除校验、待人工队列。"""

import threading
import time

from rowversion import configure, get, manual_queue, reset, update

reset()
configure(max_retries=5, backoff=0.01)

# 1) 两方并发给同一账户加钱，冲突时按 merge 合并重试
update("acct", {"balance": 100}, 0)


def deposit(amount):
    snap = get("acct")
    time.sleep(0.05)  # 放大竞态窗口，让两方读到同一版本
    result = update(
        "acct",
        {"balance": snap["row"]["balance"] + amount},
        snap["version"],
        merge=lambda current, mine: {"balance": current["balance"] + amount},
    )
    print("deposit %d ->" % amount, result)


threads = [threading.Thread(target=deposit, args=(n,)) for n in (10, 20)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print("final:", get("acct"))

# 2) 不带 merge 的过期写入直接冲突，返回当前值和版本
print("stale:", update("acct", {"balance": 0}, 1))

# 3) 删除走版本校验；旧版本不能复活已删行
print("delete stale:", update("acct", None, 1))
deleted = update("acct", None, get("acct")["version"])
print("delete ok:", deleted)
print("revive stale:", update("acct", {"balance": 1}, deleted["version"] - 1))

# 4) 重试超限：明确失败并进待人工队列
configure(max_retries=0, backoff=0)
update("hot", {"n": 1}, 0)
snap = get("hot")
update("hot", {"n": 2}, snap["version"])  # 另一方先提交
result = update("hot", {"n": 3}, snap["version"], merge=lambda cur, mine: mine)
print("exhausted:", result)
print("manual:", manual_queue())
