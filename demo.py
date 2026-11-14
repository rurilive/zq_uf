import threading
import time

from tablecopy import Table, copy, verify

# 第一段：静态全量复制
print(copy([{"id": 1}, {"id": 2}], "dst"))
print(verify())

# 第二段：复制期间源表持续写入，快照 + 增量补齐
table = Table([{"id": i} for i in range(5)], snapshot_delay=0.3)


def writer():
    time.sleep(0.1)  # 等快照位点确定后再写，模拟复制期间的并发写入
    for i in range(5, 8):
        table.write({"id": i, "v": i})


t = threading.Thread(target=writer)
t.start()
result = copy(table, "demo")
t.join()
print(result)  # snapshot_lsn=0，incremental=3，rows=8
print(verify())
