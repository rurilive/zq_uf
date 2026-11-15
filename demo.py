"""模拟一次采集任务的挂起与恢复。"""
import os
import tempfile

from crawlhold import Request, Task, hold, release

workdir = tempfile.mkdtemp(prefix="crawlhold-demo-")
task = Task("task-a", workdir=workdir)
task.proxies = ["proxy-1", "proxy-2"]
task.quota = 100
task.progress = 4
task.pending = ["http://example.com/5"]

# 一个正常收尾的在跑请求，一个卡死的在跑请求（输出写到一半）
task.in_flight.append(Request("http://example.com/4"))
half = os.path.join(workdir, "page-3.html")
with open(half, "w") as fh:
    fh.write("<html>half")
task.in_flight.append(Request("http://example.com/3", output=half, stuck=True))

print("== 挂起 ==")
held = hold(task, timeout=1.0)
print("阶段:", " -> ".join(held["phases"]))
print("释放资源:", held["released"])
print("半写文件:", held["partial_files"], "超时强制收尾:", held["timed_out"])
print("检查点:", held["checkpoint"])

print("== 恢复 ==")
resumed = release(task)
print("恢复结果:", resumed["resumed"], "进度:", resumed["progress"])
print("清理半写:", resumed["cleaned"])
print("待继续:", resumed["pending"])
