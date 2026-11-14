"""演示带可见性超时的文件队列：崩溃重投、幂等确认与死信。

运行：python3 demo.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

from filequeue import ack, configure, get, put, reset, stats

VISIBILITY_TIMEOUT = 1.0
MAX_DELIVERIES = 3


def crash_worker(data_dir, consumer):
    """取到消息后直接退出进程，模拟消费者崩溃（不 ack）。"""
    configure(data_dir, visibility_timeout=VISIBILITY_TIMEOUT,
              max_deliveries=MAX_DELIVERIES)
    msg = get(consumer)
    print("[%s] 取到消息 %s，进程崩溃（未 ack）" % (consumer, msg))
    sys.stdout.flush()
    os._exit(1)


def main():
    data_dir = tempfile.mkdtemp(prefix="filequeue-demo-")
    queue = configure(data_dir, visibility_timeout=VISIBILITY_TIMEOUT,
                      max_deliveries=MAX_DELIVERIES)
    try:
        print("== 1. 消费者崩溃，消息在不可见期内不会被别人取到 ==")
        msg_id = put({"id": 1, "body": "订单支付"})
        subprocess.run([sys.executable, __file__, "worker", data_dir, "c1"],
                       check=False)
        print("c2 立即取消息（应为 None）：", get("c2"))
        print("统计：", stats())

        print("\n== 2. 超过可见性超时，消息自动重投并幂等确认 ==")
        time.sleep(VISIBILITY_TIMEOUT + 0.1)
        redelivered = get("c2")
        print("c2 取到重投消息 id=%d，投递次数：%d"
              % (redelivered["msg_id"], redelivered["deliveries"]))
        print("重复确认（幂等，不报错）：", ack(redelivered["msg_id"]),
              ack(redelivered["msg_id"]))
        print("统计：", stats())

        print("\n== 3. 连续崩溃超过投递上限（%d 次），消息进入死信 =="
              % MAX_DELIVERIES)
        poison = put({"id": 2, "body": "谁处理谁崩溃"})
        for attempt in range(1, MAX_DELIVERIES + 1):
            subprocess.run([sys.executable, __file__, "worker", data_dir,
                            "crash-%d" % attempt], check=False)
            time.sleep(VISIBILITY_TIMEOUT + 0.1)
        print("死信后再取（应为 None）：", get("healthy"))
        print("死信消息 id：", poison, "，统计：", stats())
    finally:
        queue.close()
        shutil.rmtree(data_dir, ignore_errors=True)
        reset()


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "worker":
        crash_worker(sys.argv[2], sys.argv[3])
    else:
        main()
