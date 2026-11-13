"""模拟多个采集任务抢配额：超额排队等待，结束后输出各任务占用与等待时长。"""

import threading
import time

from crawlquota import (
    acquire,
    release,
    release_all,
    reset,
    set_cap,
    set_wait_timeout,
    stats,
    usage,
)


def main():
    reset()
    set_cap("thread", 2)
    set_cap("fd", 4)
    set_cap("memory_mb", 128)
    set_wait_timeout(5.0)
    print("配额: thread=2 fd=4 memory_mb=128 默认等待超时=5.0s")

    acquire("job-a", "thread", 2)
    acquire("job-a", "fd", 1)
    acquire("job-a", "memory_mb", 64)
    print("[job-a] 预占 thread=2 fd=1 memory_mb=64")

    def worker(name):
        print(f"[{name}] 排队等 thread ...")
        acquire(name, "thread", 1)
        print(f"[{name}] 拿到 thread=1")
        time.sleep(0.05)
        release(name, "thread", 1)

    workers = [
        threading.Thread(target=worker, args=(name,))
        for name in ("job-b", "job-c")
    ]
    for thread in workers:
        thread.start()
    time.sleep(0.2)
    print("[job-a] 释放 thread=2")
    release("job-a", "thread", 2)
    for thread in workers:
        thread.join()

    freed = release_all("job-a")
    print(f"[job-a] 结束，释放全部额度 {freed}")

    print("各任务占用与等待:")
    for name, info in stats()["tasks"].items():
        used = info["used"]
        print(
            f"任务 {name}: 占用 thread={used['thread']} fd={used['fd']} "
            f"memory_mb={used['memory_mb']}, "
            f"等待 {info['wait_seconds']:.2f}s ({info['wait_count']} 次)"
        )
    total = usage()
    print(
        f"最终全局占用: thread={total['thread']} fd={total['fd']} "
        f"memory_mb={total['memory_mb']}"
    )


if __name__ == "__main__":
    main()
