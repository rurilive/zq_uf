import multiprocessing
import os
import time

from tablelock import acquire, release, status

print(acquire("orders", "job-a", 30))
print(acquire("orders", "job-b", 30))
print(release("orders", "job-a"))


def _crashing_worker():
    acquire("orders", "job-crash", 1)  # 拿到锁后模拟崩溃：不释放直接退出
    os._exit(1)


# 第二段：进程崩溃 → 租约过期 → 被抢占
if __name__ == "__main__":
    p = multiprocessing.get_context("fork").Process(target=_crashing_worker)
    p.start()
    p.join()
    print("crashed holder:", status("orders")["owner"])

    print("waiting:", acquire("orders", "job-c", 30)["queue"])  # 未过期，进等待队列
    time.sleep(1.1)  # 等租约过期
    r = acquire("orders", "job-c", 30)
    print("preempted:", r["ok"], r["preempted"]["prev_owner"])

    acquire("orders", "job-c", 30)  # 重入
    release("orders", "job-c")
    release("orders", "job-c")
    print("final:", status("orders"))
