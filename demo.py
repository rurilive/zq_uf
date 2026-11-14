"""三条快照链演示：引用计数 + 租约 + 并发备份 + 可中断回收。"""

import shutil
import threading

from snapshotgc import (
    begin_backup,
    commit_backup,
    delete_snapshot,
    gc,
    pin,
    read_snapshot,
    reset,
    write_block,
)

STORE = "demo-store"
shutil.rmtree(STORE, ignore_errors=True)
reset(STORE)


def backup(name, parent=None, blocks=()):
    begin_backup(name, parent=parent)
    for data in blocks:
        write_block(name, data)
    commit_backup(name)


# 三条快照链：A、B 各三级，C 两级
backup("a1", blocks=[b"a-base"])
backup("a2", parent="a1", blocks=[b"a-delta-2"])
backup("a3", parent="a2", blocks=[b"a-delta-3"])
backup("b1", blocks=[b"b-base"])
backup("b2", parent="b1", blocks=[b"b-delta-2"])
backup("b3", parent="b2", blocks=[b"b-delta-3"])
backup("c1", blocks=[b"c-base"])
backup("c2", parent="c1", blocks=[b"c-delta-2"])

# 父删子读：删掉 a1/b1，子快照依然可读
delete_snapshot("a1")
delete_snapshot("b1")
print("a2 readable:", sorted(read_snapshot("a2").values()))

# 租约保护 c1，并发备份 d1 的同时跑回收
pin("c1", ttl=30)
begin_backup("d1", parent="c2")
write_block("d1", b"d-pending")
result = gc(1)  # d1 已登记未完成，回收跳过
print("gc during backup:", result)
commit_backup("d1")


# 并发压力：多线程备份 + 回收互不踩踏
def worker(i):
    for j in range(5):
        name = "t%d-%d" % (i, j)
        begin_backup(name)
        write_block(name, b"payload-%d-%d" % (i, j))
        commit_backup(name)


threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
threads.append(threading.Thread(target=lambda: [gc(2) for _ in range(5)]))
for t in threads:
    t.start()
for t in threads:
    t.join()

print("final gc:", gc(2))
shutil.rmtree(STORE, ignore_errors=True)
