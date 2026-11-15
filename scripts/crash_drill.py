#!/usr/bin/env python3
"""写租约跨进程崩溃演练。

场景：
  1. worker 子进程拿到租约、写一行数据，然后 os._exit 硬退出（模拟崩溃，
     不调用 release）；
  2. 父进程从另一个进程确认租约状态可见、持有者仍是 worker-A；
  3. 等租约过期后父进程接管，确认返回里 taken_over=True；
  4. 接管者写入新数据，再用旧 fencing token 模拟"僵尸"迟到写入，
     必须被拒绝，最终数据不被覆盖。

用法：
  python3 scripts/crash_drill.py drill  <db_path> [ttl]
  python3 scripts/crash_drill.py worker <db_path> [ttl]
  python3 scripts/crash_drill.py status <db_path> <table>
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tablelease import (  # noqa: E402
    FenceRejected,
    FencedWriter,
    acquire,
    audit_log,
    configure,
    status,
)

TABLE = "orders"
ROW = "row-1"


def worker(db_path, ttl):
    """拿租约、写数据，然后硬退出——不释放，模拟进程崩溃。"""
    configure(db_path=db_path)
    resp = acquire(TABLE, "worker-A", ttl)
    if not resp["granted"]:
        raise RuntimeError(f"worker 未能获取租约: {resp}")
    writer = FencedWriter(db_path)
    writer.write(ROW, "A-v1", resp["fence_token"])
    # 硬退出：不执行 finally、不 release，模拟段错误 / kill -9。
    os._exit(0)


def drill(db_path, ttl):
    configure(db_path=db_path)

    # 1. 启动 worker 子进程并等它"崩溃"。
    proc = subprocess.run(
        [sys.executable, __file__, "worker", db_path, str(ttl)],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"worker 异常退出: {proc.stderr}")

    # 2. 跨进程查看：另一个进程必须能看到 worker-A 持有的租约。
    visible = False
    for _ in range(50):
        st = status(TABLE)
        if st["holder"] == "worker-A":
            visible = True
            break
        time.sleep(0.05)
    if not visible:
        raise RuntimeError("跨进程看不到 worker-A 的租约状态")

    # 3. 等待租约自然到期。
    time.sleep(ttl + 0.5)
    expired_state = status(TABLE)
    if not expired_state["expired"]:
        raise RuntimeError("租约未按预期过期")
    old_token = expired_state["fence_token"]

    # 4. 接管：必须确认原持有者已过期。
    takeover = acquire(TABLE, "worker-B", ttl * 10)
    if not takeover["granted"] or not takeover["taken_over"]:
        raise RuntimeError(f"接管失败: {takeover}")

    # 5. 接管者写入新数据。
    writer = FencedWriter(db_path)
    writer.write(ROW, "B-v2", takeover["fence_token"])

    # 6. 旧持有者"复活"，拿旧 token 迟到写入 —— 必须被 fencing 拒绝。
    stale_rejected = False
    try:
        writer.write(ROW, "A-STALE", old_token)
    except FenceRejected:
        stale_rejected = True

    final_row = writer.read(ROW)
    events = [e["event"] for e in audit_log(TABLE)]
    summary = {
        "takeover_granted": takeover["granted"],
        "previous_owner": takeover["previous_owner"],
        "new_holder": takeover["holder"],
        "stale_write_rejected": stale_rejected,
        "final_payload": final_row["payload"],
        "final_fence_token": final_row["fence_token"],
        "cross_process_status_visible": visible,
        "audit_events": events,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if (stale_rejected and final_row["payload"] == "B-v2"
                 and "takeover" in events) else 1


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    command, db_path = argv[1], argv[2]
    if command == "worker":
        ttl = float(argv[3]) if len(argv) > 3 else 1.0
        worker(db_path, ttl)
        return 0
    if command == "drill":
        ttl = float(argv[3]) if len(argv) > 3 else 1.0
        return drill(db_path, ttl)
    if command == "status":
        table = argv[3]
        configure(db_path=db_path)
        print(json.dumps(status(table), ensure_ascii=False, indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
