"""演示：并发备份下的带租约回收。

一个备份线程边回收边登记新分区（先 pin 再写），主线程分批回收。
"""

import tempfile
import threading
import time

from partgc2 import gc, pin, release, reset

state = tempfile.mkdtemp(prefix="partgc2-demo-")
reset()

existing = ["ds/p1", "ds/p2", "ds/p3", "ds/p4"]
pin("ds/p2", holder="reader-1", ttl=60)  # 正在读的分区

started = threading.Event()


def backup():
    started.wait(5)
    pin("ds/p5", holder="backup-1", kind="backup", ttl=60)  # 先登记
    time.sleep(0.3)  # 再写（写期间回收会跳过它）
    release("ds/p5", holder="backup-1")


def deleter(name):
    started.set()
    time.sleep(0.05)  # 模拟慢删除，给备份线程登记窗口
    print("delete", name)


t = threading.Thread(target=backup)
t.start()
result = gc({
    "existing": existing + ["ds/p5"],
    "keep": [],
    "batch_size": 2,
    "deleter": deleter,
    "state_dir": state,
})
t.join()

print("deleted:", result["deleted"])
print("skipped:", [(s["partition"], s["holder"], s["kind"]) for s in result["skipped"]])
print("batches:", result["batches"], "paused:", result["paused"])
print("leases:", result["leases"])
print("audit:", result["audit"])
print("state dir:", state)
