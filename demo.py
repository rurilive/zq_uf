from datetime import datetime, timedelta, timezone

from crontick import next_run, tick

print(next_run("shop", datetime(2026, 1, 1, 8, 0, 0)))
print(tick(datetime(2026, 1, 1, 8, 0, 0), lambda task: print("run", task)))

# 第二段：两实例同一分钟启动，共享文件租约互斥 + 错峰 + 错过补偿
import tempfile

from crontick import FileStore, Scheduler

tasks = {"shop": {"interval": 60, "tz": "Asia/Shanghai", "jitter_window": 20}}
store = FileStore(tempfile.mkdtemp(prefix="crontick-demo-"))
a = Scheduler(tasks, store=store, instance_id="instance-A")
b = Scheduler(tasks, store=store, instance_id="instance-B")

start = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
print("A next_run:", a.next_run("shop", start))
print("B next_run:", b.next_run("shop", start))  # 两实例错峰到不同时刻

due = start + timedelta(seconds=30)  # 双方窗口（20s）都已过
print("A tick:", a.tick(due, lambda task: print("run", task)))
print("B tick:", b.tick(due, lambda task: print("run", task)))  # 租约互斥，只有一个 ran

# A “挂掉” 3 分钟后恢复：按最近一次应触发时间补跑一次，不重复补
later = start + timedelta(minutes=3, seconds=30)
print("A 恢复补跑:", a.tick(later, lambda task: print("run", task)))
print("A 重复 tick:", a.tick(later, lambda task: print("run", task)))
