"""demo 的子进程工人：接收链路标识，在独立进程里记录 fetch 阶段。"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawlchain import start


def main():
    task, trace_id, attempt = sys.argv[1], sys.argv[2], int(sys.argv[3])
    data_dir = sys.argv[4] if len(sys.argv) > 4 else None
    tr = start(task, trace_id=trace_id, attempt=attempt, data_dir=data_dir)
    with tr.stage("fetch"):
        time.sleep(0.05)  # 模拟抓取
    print(f"worker pid={os.getpid()} trace={trace_id} attempt={attempt} fetch done")


if __name__ == "__main__":
    main()
