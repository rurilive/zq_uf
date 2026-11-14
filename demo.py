"""双写冲突模拟：两个任务写同一分区，栅栏挡住旧写。"""

import threading
import time

from partitionlease import (
    LeaseError,
    acquire,
    events,
    holder,
    renew,
    write,
)

DATASET = "orders"
PARTITION = "dt=2026-01-01"


def job(name, writes):
    lease = acquire(DATASET, PARTITION, name, 0.2)
    print(f"[{name}] 拿到租约 fence={lease['fence_token']}")
    for payload in writes:
        try:
            write(DATASET, PARTITION, name, lease["fence_token"], payload)
            print(f"[{name}] 写入成功 {payload}")
        except LeaseError as exc:
            print(f"[{name}] 写入被拒 {type(exc).__name__}: {exc}")
        time.sleep(0.05)


def main():
    a = threading.Thread(target=job, args=("job-a", ["a1", "a2", "a3"]))
    b = threading.Thread(target=job, args=("job-b", ["b1", "b2"]))
    a.start()
    time.sleep(0.08)  # a 先写一笔，b 再进场抢占
    b.start()
    a.join()
    b.join()

    print("\n持有者:", holder(DATASET, PARTITION))
    print("续期(旧持有者 job-a):", renew(DATASET, PARTITION, "job-a", 1))
    print("事件记录:")
    for e in events(DATASET, PARTITION):
        print(f"  {e['event']:<14} owner={e['owner']:<6} fence={e['fence_token']}")


if __name__ == "__main__":
    main()
