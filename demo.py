"""模拟站点抖动：shop 站时好时坏，多个采集任务失败后在共享窗口内重试。"""

from retrywindow import abandoned, cancel, due, schedule, windows
from retrywindow import retry

retry._rng.seed(7)  # 固定抖动，输出可复现

now = [0.0]
retry._clock = lambda: now[0]  # 假时钟，按轮推进

tasks = ["task-1", "task-2", "task-3"]

for round_no in range(1, 6):
    now[0] += 5.0  # 站点抖动：每 5 秒又有一批任务失败
    print(f"--- 第 {round_no} 轮失败, t={now[0]:.0f}s ---")
    for task in tasks:
        try:
            print(schedule(task, "shop"))
        except retry.RetryWindowError as exc:
            print(f"{task}: {exc}")
    if round_no == 1:
        print("取消 task-3:", cancel("task-3"))  # 取消后不再触发
    print("待重试:", due())
    print("到期任务:", due(now[0] + 2.0))
    print("共享窗口:", windows())

print("放弃列表:", abandoned())
