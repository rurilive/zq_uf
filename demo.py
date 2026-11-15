"""jobrecover 演示：阶段级恢复、损坏回退、任务隔离。

直接运行：python3 demo.py
"""

import json
import logging
import os
import tempfile

from jobrecover import JobRecovery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

PAGES = 5
STORE_CRASH_AT = 3  # 模拟 store 阶段处理第 3 页时采集任务中途失败


def run(recovery, job, crash_page=None):
    """按 resume 给出的下一阶段推进；中途抛异常则当次保存不生效、不推进。"""
    while True:
        point = recovery.resume(job)
        if point["finished"]:
            print("[%s] 恢复结果：全部阶段已完成，stored_pages=%s"
                  % (job, point["stored_pages"]))
            return point

        stage = point["stage"]
        print("[%s] 进入阶段 %s（已完成 %s，已入库 %s）"
              % (job, stage, point["completed"], point["stored_pages"]))

        if stage == "start":
            with recovery.stage(job, stage) as ctx:
                ctx.state = {"pages": PAGES, "position": 0}

        elif stage == "fetch":
            with recovery.stage(job, stage) as ctx:
                ctx.position = PAGES
                ctx.state["urls"] = ["https://example.test/page/%d" % p
                                     for p in range(1, PAGES + 1)]

        elif stage == "parse":
            with recovery.stage(job, stage) as ctx:
                ctx.position = PAGES
                ctx.state["items"] = [{"page": p} for p in range(1, PAGES + 1)]

        elif stage == "store":
            # 已入库页面来自检查点；每入库一页立即用 complete=False 推进位点，
            # 中途失败后恢复时这些页面不会重复入库；全部入库后再标记阶段完成
            stored = set(point["stored_pages"])
            for page in range(1, PAGES + 1):
                if page in stored:
                    print("[%s] 跳过已入库页面 %d" % (job, page))
                    continue
                if crash_page == page:
                    raise RuntimeError("模拟采集中途失败：写入页面 %d 时进程退出" % page)
                print("[%s] 页面 %d 入库" % (job, page))
                stored.add(page)
                recovery.save(job, stage, {
                    "position": page,
                    "pages": sorted(stored),
                }, complete=False)
            recovery.save(job, stage, {
                "position": PAGES,
                "pages": sorted(stored),
            })  # complete=True：store 阶段正式完成

        elif stage == "finish":
            with recovery.stage(job, stage) as ctx:
                ctx.state = {"ok": True, "position": PAGES}


def main():
    cp_dir = tempfile.mkdtemp(prefix="jobrecover-demo-")
    recovery = JobRecovery(cp_dir)
    print("检查点目录（按任务划分）：%s\n" % cp_dir)

    # 1) 第一次运行：store 阶段中途失败，已入库 1、2 两页
    print("== 场景一：采集中途失败 ==")
    try:
        run(recovery, "job-a", crash_page=STORE_CRASH_AT)
    except RuntimeError as exc:
        print("任务失败：%s\n" % exc)

    # 2) 恢复重跑：只跑未完成的 store/finish，页面 1、2 不重复入库
    print("== 场景二：阶段级恢复 ==")
    run(recovery, "job-a")
    again = recovery.resume("job-a")
    same = recovery.resume("job-a")
    print("重复恢复结果一致：%s\n" % (again == same))

    # 3) 检查点写一半损坏：回退上一有效点
    print("== 场景三：检查点损坏回退 ==")
    recovery.save("job-b", "start", {})
    recovery.save("job-b", "fetch", {"position": 4})
    job_b_dir = os.path.join(cp_dir, "job-b")
    with open(os.path.join(job_b_dir, "current.json")) as handle:
        current = json.loads(handle.read())["file"]
    with open(os.path.join(job_b_dir, current), "wb") as handle:
        handle.write(b'{"stage": "fetch", "half-written": ')  # 写一半
    point = recovery.resume("job-b")
    print("回退标志 rollback=%s，恢复到 version=%s，下一阶段=%s\n"
          % (point["rollback"], point["version"], point["stage"]))

    # 4) 并发采集任务隔离：各用各的目录，互不覆盖
    print("== 场景四：按任务隔离 ==")
    recovery.save("job-x", "start", {})
    recovery.save("job-x", "fetch", {"position": 6})
    recovery.save("job-x", "parse", {"position": 6})
    recovery.save("job-y", "start", {})
    recovery.save("job-y", "fetch", {"position": 2})
    print("目录列表：%s" % sorted(os.listdir(cp_dir)))
    print("job-x 恢复：stage=%s position=%s" %
          (recovery.resume("job-x")["stage"], recovery.resume("job-x")["position"]))
    print("job-y 恢复：stage=%s position=%s" %
          (recovery.resume("job-y")["stage"], recovery.resume("job-y")["position"]))


if __name__ == "__main__":
    main()
