"""演示：动态超时 + 现场保存 + 断点恢复 + 统计输出。"""

import json

from timeoutgov import TaskTimeoutError, dump_state, run, set_clock, stats

now = [0]
set_clock(lambda: now[0])


def work_after(seconds, value="ok"):
    def work():
        now[0] += seconds
        return value

    return work


# 1) 历史耗时：bulk 任务历来 40s，tiny 任务历来 2s
for i in range(3):
    run({"name": f"bulk-{i}", "type": "bulk", "work": work_after(40)}, deadline=10_000)
    run({"name": f"tiny-{i}", "type": "tiny", "work": work_after(2)}, deadline=10_000)

# 2) 大任务 45s：固定 30s 会误杀，动态超时（40*1.5=60s）放行
print("大任务完成:", run({"name": "bulk-big", "type": "bulk", "work": work_after(45)})["result"])

# 3) 小任务卡住：动态超时 3s，保存进度和堆栈现场
progress = {"done": 0}


def hanging():
    for _ in range(3):
        now[0] += 1
        progress["done"] += 1
    now[0] += 100  # 卡住


try:
    run({"name": "tiny-stuck", "type": "tiny", "progress": progress, "work": hanging})
except TaskTimeoutError as exc:
    saved = exc.checkpoint
    print(f"已保存现场: task={saved['task']} limit={exc.limit}s "
          f"progress={saved['progress']} stack_frames={len(saved['stack'])}")


# 4) 断点恢复：从 done=3 继续，不重跑
def resume_work(checkpoint):
    done = checkpoint["progress"]["done"]
    while done < 10:
        now[0] += 1
        done += 1
    return f"done={done}"


result = run({"name": "tiny-stuck", "type": "tiny",
              "checkpoint": saved, "work": resume_work}, deadline=30)
print("断点恢复:", result["result"], "resumed=", result["resumed"])

# 5) 统计输出
report = stats()
print("超时任务:", len(report["timeouts"]), [t["name"] for t in report["timeouts"]])
print("耗时分布:", {k: {"count": v["count"], "mean": v["mean"]}
                    for k, v in report["durations"].items()})
print("恢复结果:", len(report["recoveries"]), [r["name"] for r in report["recoveries"]])
print("状态可序列化:", len(json.dumps(dump_state())), "bytes")
