import threading
import time

from crawlbarrier import arrive, expect, wait

# 场景一：分片失败，整批失败并按写入逆序补偿
expect("batch-1", "shard-a", "shard-b")
arrive("batch-1", "shard-a", compensate=lambda: "undo-a")
arrive("batch-1", "shard-b", ok=False, compensate=lambda: "undo-b")
result = wait("batch-1", timeout=1)
print("batch-1 ok:", result.ok, "failed:", result.failed)
print("batch-1 compensation:", result.compensation)

# 场景二：一个分片超时，wait 返回未到达分片
expect("batch-2", "shard-a", "shard-b", "shard-c")
arrive("batch-2", "shard-a")
arrive("batch-2", "shard-b")


def slow_shard():
    time.sleep(2)
    arrive("batch-2", "shard-c")


threading.Thread(target=slow_shard, daemon=True).start()
result = wait("batch-2", timeout=0.5)
print("batch-2 ok:", result.ok, "timed_out:", result.timed_out)
print("batch-2 status:", result.status)
