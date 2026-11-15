from smallfile import cleanup_history, compact, plan, retry_failed, rollback


def make_part(name, sizes, active_reads=0, fail_merge=False):
    return {
        "name": name,
        "files": [{"path": "{}/f{}.dat".format(name, i), "size": s}
                  for i, s in enumerate(sizes)],
        "manifest": ["{}/f{}.dat".format(name, i) for i in range(len(sizes))],
        "active_reads": active_reads,
        "fail_merge": fail_merge,
        "history": [],
    }


dataset = {"partitions": [
    make_part("part-day1-hour01", [1024] * 6),                    # 小文件，可合并
    make_part("part-day1-hour02", [1024] * 4, active_reads=1),    # 有活跃读取，跳过
    make_part("part-day1-hour03", [64 * 1024 * 1024] * 3),        # 文件够大，不合并
    make_part("part-day1-hour04", [1024] * 5, fail_merge=True),   # 合并会失败
]}

# 第一段：先在一个小分区试运行，确认替换和清单更新不影响正在跑的查询
trial = plan(dataset, only=["part-day1-hour01"])
print("trial:", compact(trial))

# 第二段：全量合并，活跃分区跳过，失败保留原文件并单独重试
result = compact(plan(dataset))
print("merged:", result["merged"])
print("skipped:", result["skipped"])
print("replaced:", result["replaced"])
print("failed:", result["failed"])
print("file_counts:", result["file_counts"])

dataset["partitions"][3]["fail_merge"] = False
print("retry:", retry_failed(dataset, result["failed"])["merged"])

# 回退与历史清理
print("rollback:", rollback(dataset["partitions"][0], trial["batch_id"]))
print("cleanup:", cleanup_history(dataset["partitions"]))
