"""两个进程抢同一任务：后者领到更高栅栏值，旧持有者续期被拒。

用法：python3 demo.py
"""

import os
import subprocess
import sys
import tempfile
import time

TASK_ID = "task-1"


def worker(name):
    from crawllease import claim, renew, status

    mine = claim(TASK_ID, name, 1.5)
    print(f"[{name}] 领到租约 fence={mine['fence']}", flush=True)
    time.sleep(0.6)  # 模拟抓取中，期间对方已抢走租约
    ok = renew(TASK_ID, name, 1.5)
    current = status(TASK_ID)
    print(f"[{name}] 续期结果={ok} 当前持有者={current['owner']} "
          f"fence={current['fence']}", flush=True)


def main():
    fd, state = tempfile.mkstemp(prefix="crawllease-demo-", suffix=".json")
    os.close(fd)
    os.unlink(state)
    env = dict(os.environ, CRAWLLEASE_STATE=state)
    procs = [
        subprocess.Popen([sys.executable, __file__, "worker", name], env=env)
        for name in ("worker-a", "worker-b")
    ]
    for proc in procs:
        proc.wait()

    os.environ["CRAWLLEASE_STATE"] = state
    from crawllease import reclaims, status

    print("最终持有者:", status(TASK_ID))
    print("回收记录:", reclaims())
    for suffix in ("", ".lock"):
        try:
            os.unlink(state + suffix)
        except OSError:
            pass


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "worker":
        worker(sys.argv[2])
    else:
        main()
