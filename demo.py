"""目录探测演示：自适应并发、拦截识别、断点续跑、命中归类。

直接运行：python demo.py
每个场景用独立的临时落盘目录，互不干扰。
"""

import shutil
import tempfile

from dirprobe import load, probe, report, reset


def show(title, out):
    print(f"-- {title}")
    print("   命中:", [h["path"] for h in out["hits"]],
          "| 拦截次数:", out["blocked"],
          "| 当前并发:", out["concurrency"],
          "| 续跑位置:", out["resume"],
          "| 停止:", out["stopped"])


# 场景一：429 拦截页不算命中，并发随拦截下调。
state_dir = tempfile.mkdtemp(prefix="dirprobe-demo1-")
words = ["admin", "backup.zip", "login", "static"]
responses = {
    "admin": {"status": 200},
    "backup.zip": {"status": 200},
    "login": {"status": 403},
    "static": {"status": 429, "body": "too many requests"},
}
print("字典条数", load("shop", words, responses, state_dir=state_dir)["words"])
out = probe("shop", concurrency=8)
show("第一轮：static 被拦，不计命中，并发下调", out)

# 场景二：中断后续跑——已确认的路径不再请求，只补被拦的空洞。
responses["static"] = {"status": 200}
load("shop", words, responses, state_dir=state_dir)  # 模拟进程重启后重新 load
out = probe("shop", concurrency=8)
show("第二轮：从续跑位置继续，static 放行后命中", out)
rep = report("shop")
print("   报告进度:", rep["progress"])
print("   命中分类:", {k: [h["path"] for h in v] for k, v in rep["groups"].items()})
print("   被拦截路径(单独标注):", rep["blocked_paths"])
shutil.rmtree(state_dir, ignore_errors=True)

# 场景三：同一路径的不同参数合并成一条。
state_dir = tempfile.mkdtemp(prefix="dirprobe-demo2-")
words = ["search?q=1", "search?q=2&page=3", "assets/", "db/backup.sql"]
responses = {word: {"status": 200} for word in words}
load("wiki", words, responses, state_dir=state_dir)
probe("wiki")
rep = report("wiki")
print("\n参数合并与归类:")
for category, entries in rep["groups"].items():
    for entry in entries:
        print(f"   [{category}] {entry['path']} 参数={entry['params']} 合并{entry['count']}条")
shutil.rmtree(state_dir, ignore_errors=True)

# 场景四：站点整体拒绝访问——停止本轮并保留进度，拒绝页不算空结果。
state_dir = tempfile.mkdtemp(prefix="dirprobe-demo3-")
words = [f"p{i}" for i in range(8)]
responses = {word: {"status": 429, "body": "访问频繁"} for word in words}
load("down", words, responses, state_dir=state_dir)
out = probe("down", concurrency=8)
show("整站拒绝", out)
print("   被拦截批次:", [(b["batch"], b["paths"]) for b in out["blocked_batches"]])
reset("down")  # 清空进度，目标恢复后可从头再探
shutil.rmtree(state_dir, ignore_errors=True)
