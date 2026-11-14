"""crawlpause 演示：按任务暂停 -> 检查点 -> 恢复，含超时中断场景。

运行：python3 demo.py
"""

import json
import os
import tempfile
import threading
import time

from crawlpause import Coordinator, CrawlTask


def banner(title):
    print("\n" + "=" * 8 + " " + title + " " + "=" * 8)


def show(title, obj):
    print("[%s] %s" % (title, json.dumps(obj, ensure_ascii=False, sort_keys=True)))


def main():
    ckpt_dir = tempfile.mkdtemp(prefix="crawlpause-demo-")
    print("检查点目录:", ckpt_dir)
    coord = Coordinator(checkpoint_dir=ckpt_dir)

    # ------------------------------------------------------------ 场景一：优雅收尾
    banner("场景一：暂停时当前批次能在超时内自然收尾")
    gate = threading.Event()

    def slow_processor(item):
        # 第一批的 k1 故意慢一点，保证暂停时批次还在跑
        if item["key"] == "k1":
            gate.wait(timeout=5)
        return "value-of-" + str(item["key"])

    task_a = CrawlTask(
        "task-A",
        [{"key": "k%d" % i, "data": i} for i in range(6)],
        processor=slow_processor,
        batch_size=3,
        coordinator=coord,
    )
    task_a.start()
    task_a.wait_batch_started()
    time.sleep(0.05)  # 让 k0 写入、k1 进入处理器

    show("暂停前状态", coord.status("task-A"))
    print(">>> pause(task-A)：先置收尾态（不开新批次），等当前批次结束")
    gate.set()  # 放行卡住的处理器，批次得以优雅收尾
    pause_result = coord.pause("task-A", drain_timeout=3)
    show("暂停结果（暂停阶段+检查点）", pause_result)
    show("暂停后状态", coord.status("task-A"))
    with open(pause_result["checkpoint"]["path"], encoding="utf-8") as fh:
        print("检查点文件内容:\n" + fh.read())

    print(">>> 重复 pause 是幂等的，阶段不变、检查点不重写")
    show("再次 pause", coord.pause("task-A"))

    print(">>> resume(task-A)：从检查点游标继续")
    resume_result = coord.resume("task-A")
    show("恢复结果", resume_result)
    task_a.wait_done(5)
    show("最终统计（6 条全部入库，无重复）", task_a.stats())

    # ------------------------------------------------------------ 场景二：超时中断 + 幂等重放
    banner("场景二：批次收尾超时 -> 中断记录 -> 恢复重放，幂等键去重")
    block = threading.Event()

    def blocking_processor(item):
        if item["key"] == "k4":  # 第二批：k3 已写，k4 卡住
            block.wait(timeout=10)
        return "value-of-" + str(item["key"])

    task_b = CrawlTask(
        "task-B",
        [{"key": "k%d" % i, "data": i} for i in range(6)],
        processor=blocking_processor,
        batch_size=3,
        coordinator=coord,
    )
    task_b.start()

    def wait_until(predicate, what):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise RuntimeError("timeout waiting for " + what)

    wait_until(lambda: task_b.stats()["safe_cursor"] >= 1, "第一批完成")
    wait_until(lambda: task_b.stats()["processed"] >= 4, "k3 已写入")
    show("中断前统计（第一批 3 条 + 第二批 k3 已写）", task_b.stats())

    print(">>> pause(task-B, drain_timeout=0.3)：等不到收尾，超时中断并记录")
    pause_b = coord.pause("task-B", drain_timeout=0.3)
    show("暂停结果（interrupted=true，检查点仍在批次边界）", pause_b)
    show("暂停后状态", coord.status("task-B"))

    block.set()  # 放行，旧工作线程退出
    task_b.wait_done(2)

    print(">>> resume(task-B)：重放第二批，k3 命中幂等键被跳过")
    resume_b = coord.resume("task-B")
    show("恢复结果", resume_b)
    task_b.wait_done(5)
    show("最终统计（stored=6 不重复，duplicates=1 为重放去重）", task_b.stats())

    # ------------------------------------------------------------ 场景三：多任务独立 + 异常路径
    banner("场景三：多任务独立控制与异常路径")
    task_c = CrawlTask(
        "task-C", [{"key": "c1", "data": 1}],
        batch_size=1, coordinator=coord,
    )
    task_c.start()
    task_c.wait_done(2)
    coord.pause("task-C")
    print("task-B 阶段:", coord.status("task-B")["phase"],
          "| task-C 阶段:", coord.status("task-C")["phase"])
    coord.resume("task-C")

    try:
        coord.resume("task-never-existed")
    except Exception as exc:
        print(">>> 恢复未知任务的明确报错: %s: %s"
              % (type(exc).__name__, exc))

    task_d = CrawlTask(
        "task-D", [{"key": "d1", "data": 1}],
        batch_size=1, coordinator=coord,
    )
    task_d.start()
    task_d.wait_done(2)
    try:
        coord.resume("task-D")  # task-D 一直在 running，从未暂停
    except Exception as exc:
        print(">>> 恢复未暂停任务的明确报错: %s: %s"
              % (type(exc).__name__, exc))

    print("\n检查点目录:", ckpt_dir)
    print("目录文件:", sorted(os.listdir(ckpt_dir)))


if __name__ == "__main__":
    main()
