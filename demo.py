"""模拟两个入库进程写同一张表：etl-a 拿到租约后崩溃，etl-b 排队并抢占。"""

import json
import multiprocessing
import os
import tempfile

os.environ["TABLELEASE_DIR"] = tempfile.mkdtemp(prefix="tablelease-demo-")

from tablelease import acquire, release, status, audit_log


def crashed_writer():
    # 入库进程拿到租约后崩溃：os._exit 跳过一切清理，不释放租约
    print("[etl-a] 获取:", acquire("orders", "etl-a", 30), flush=True)
    os._exit(1)


def waiting_writer():
    # 持有者已崩溃且未释放，排队后被判定 holder_dead 并抢占
    print("[etl-b] 获取(等待+抢占):", acquire("orders", "etl-b", 30))
    print("[etl-b] 重入续约:", acquire("orders", "etl-b", 30))
    print("[etl-b] 部分释放:", release("orders", "etl-b"))
    print("[etl-b] 释放:", release("orders", "etl-b"))
    print("[etl-b] 重复释放(幂等):", release("orders", "etl-b"))


if __name__ == "__main__":
    ctx = multiprocessing.get_context("fork")

    a = ctx.Process(target=crashed_writer, name="etl-a")
    a.start()
    a.join()
    print("etl-a 退出码:", a.exitcode, "（崩溃，租约未释放）")

    b = ctx.Process(target=waiting_writer, name="etl-b")
    b.start()
    b.join()

    print("最终租约状态:", json.dumps(status("orders"), ensure_ascii=False))
    print("抢占审计:", json.dumps(audit_log(), ensure_ascii=False))
    print("租约目录:", os.environ["TABLELEASE_DIR"],
          "（任意进程可执行 python3 -m tablelease 查看）")
