"""模拟：大租户批量提交 vs 小租户高优任务。

演示配额限流、低优保底、高优抢占与进度续跑，最后输出统计并留档。
"""

from jobpriority import (
    configure,
    dump_log,
    next_job,
    release,
    report_progress,
    reset,
    stats,
    submit,
)

# 第一段：默认配置退化为先进先出（保持原演示）
submit({"id": 1})
submit({"id": 2})
print(next_job())
reset()

# 第二段：2 个执行位；bigco 每窗口最多 3 次出队；优先级 0 保底 1 个执行位
configure(capacity=2, window_seconds=60.0,
          quotas={"bigco": 3}, guarantees={0: 1})

for i in range(6):  # 大租户批量低优任务
    submit({"id": "big-%d" % i, "tenant": "bigco", "priority": 1})
submit({"id": "small-batch", "tenant": "smallco", "priority": 0})

running = []
for _ in range(2):  # 占满执行位：bigco 被配额限流，小租户保底上位
    job = next_job()
    if job:
        running.append(job)
print("占用执行位:", [j["id"] for j in running])

big_job = next(j for j in running if j["tenant"] == "bigco")
report_progress(big_job["id"], 0.7)  # 大租户任务跑到 70%
submit({"id": "small-urgent", "tenant": "smallco", "priority": 9})
urgent = next_job()  # 高优抢占低优，被抢占任务带 70% 进度回队
print("高优出队:", urgent["id"])

release(urgent["id"])
resumed = next_job()  # 被抢占任务续跑，进度不丢
print("续跑任务:", resumed["id"], "进度:", resumed["progress"])

s = stats()
print("配额使用:", s["quota"])
print("抢占次数:", s["preemptions"]["count"])
print("等待统计:", {"count": s["wait"]["count"], "max": s["wait"]["max"]})

n = dump_log("scheduler_log.jsonl")  # 留档
print("已留档 %d 条事件到 scheduler_log.jsonl" % n)
