"""演示：上游延迟、依赖等待、错过补跑、并发上限、失败告警与关闭清理。

运行：python3 demo.py
"""

import time

from jobwindow import (
    close,
    complete_task,
    fail_job,
    mark_job,
    open,
    reset,
    status,
)


def show(title, result):
    print("\n== %s ==" % title)
    for key in (
        "window",
        "opened",
        "waited",
        "wait_seconds",
        "catch_up",
        "catch_up_reason",
        "concurrency",
        "group",
        "reason",
        "alerts",
    ):
        if key in result:
            print("  %-16s %s" % (key, result[key]))


reset()
now = time.time()

# 1) 上游延迟 0.3s，窗口等待后开启
started = time.monotonic()

def upstream_done_after(delay):
    deadline = time.monotonic() + delay

    def check():
        return time.monotonic() >= deadline

    return check

show(
    "1. 上游未完成时等待（上游延迟 0.3s）",
    open(
        {
            "name": "hourly",
            "upstream": [{"name": "extract", "check": upstream_done_after(0.3)}],
            "dependency_policy": "wait",
            "wait_timeout": 2.0,
            "resource_group": "etl",
            "max_concurrency": 1,
            "tasks": ["extract", "transform"],
        }
    ),
)
print("  实际耗时          %.2fs" % (time.monotonic() - started))
complete_task({"name": "hourly"}, "extract")

# 2) 同资源组第二个窗口被并发上限挡住
show(
    "2. 资源组 etl 并发上限 1，第二个窗口被跳过",
    open(
        {
            "name": "hourly-2",
            "resource_group": "etl",
            "max_concurrency": 1,
        }
    ),
)

# 3) 错过窗口，补跑一次
show(
    "3. 错过窗口补跑（只补一次）",
    open(
        {
            "name": "daily",
            "scheduled_at": now - 3600,
            "window_seconds": 60,
            "resource_group": "etl",
        }
    ),
)
close({"name": "daily"})
show(
    "4. 同一场次再次触发，不再补跑",
    open(
        {
            "name": "daily",
            "scheduled_at": now - 3600,
            "window_seconds": 60,
            "resource_group": "etl",
        }
    ),
)

# 5) 上游失败：下游不启动并告警
fail_job("nightly-load", "校验失败：主键冲突")
show(
    "5. 上游失败，下游不启动并告警",
    open(
        {
            "name": "report",
            "upstream": ["nightly-load"],
            "resource_group": "etl",
        }
    ),
)

# 6) 关闭窗口清理未完成任务并释放并发
print("\n== 6. 关闭窗口 ==")
print("  close(hourly) ->", close({"name": "hourly"}))
print("  状态查询      ->", status({"name": "hourly"}))

# 上游恢复后，下游可正常开启
mark_job("nightly-load", "completed")
show(
    "7. 上游恢复后下游开启",
    open({"name": "report", "upstream": ["nightly-load"], "resource_group": "etl"}),
)
close({"name": "report"})
close({"name": "hourly-2"})
close({"name": "daily"})
