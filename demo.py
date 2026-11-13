import threading
import time
import random

from idemwrite import get_stats, upsert
from idemwrite.core import VERSION_FIELD, _state_of

# 1) 基础流程：插入 -> 新版本覆盖 -> 同版本重复跳过 -> 旧版本回退忽略
store = {}
upsert([{"id": 1, "v": "a", "version": 1}], ["id"], store)
upsert([{"id": 1, "v": "b", "version": 2}], ["id"], store)
upsert([{"id": 1, "v": "a", "version": 2}], ["id"], store)  # 重复
upsert([{"id": 1, "v": "old", "version": 1}], ["id"], store)  # 回退
print("serial  :", store[(1,)], get_stats(store))

# 2) 并发写同一批业务键：4 个线程各写版本 1..4，最终只保留最高版本
store = {}
key_count = 5
barrier = threading.Barrier(4)


def worker(version):
    barrier.wait()
    time.sleep(random.uniform(0, 0.005))  # 同时放行后随机交错
    rows = [
        {"id": index, "v": "w%d" % version, "version": version}
        for index in range(key_count)
    ]
    upsert(rows, ["id"], store, batch_size=2)


threads = [
    threading.Thread(target=worker, args=(v,)) for v in range(1, 5)
]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

print("concurrent:", [store[(index,)]["version"] for index in range(key_count)])
print("stats   :", get_stats(store))

# 3) 条件更新冲突：判定后、提交前另一写者先插了同键，重读再判
store = {}


def racer(batch_index):
    if not getattr(racer, "fired", False):
        racer.fired = True
        store[(7,)] = {"id": 7, "v": "racer", VERSION_FIELD: 9}
        _state_of(store).versions[(7,)] = 9


upsert([{"id": 7, "v": "late", "version": 8}], ["id"],
       store, on_batch=racer)
print("conflict :", store[(7,)], get_stats(store))
