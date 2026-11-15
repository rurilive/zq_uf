"""演示：正常采集 -> 模拟崩溃（日志半行）-> 位点恢复 -> 幂等/跳过/中断续跑。"""

import importlib
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["CRAWLRECOVER_HOME"] = os.path.join(HERE, ".crawlrecover-demo")
shutil.rmtree(os.environ["CRAWLRECOVER_HOME"], ignore_errors=True)

from crawlrecover import mark, recover  # noqa: E402

recover_mod = importlib.import_module("crawlrecover.recover")
JOURNAL = os.path.join(os.environ["CRAWLRECOVER_HOME"], "task-a.journal")


def show(title, report):
    print("== {} ==".format(title))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


# 1. 正常采集：完成 page-1 ~ page-3，位点随写随落盘
for p in ("page-1", "page-2", "page-3"):
    mark("task-a", p)

# 2. 模拟崩溃：page-4 写到一半进程被杀，日志留下半行
with open(JOURNAL, "ab") as f:
    f.write(b'{"seq": 4, "batch": "b-cra')

# 3. 重启恢复：截断半行，输出位点、跳过和截断记录
r1 = recover("task-a")
show("崩溃后恢复（截断半行）", r1)

# 4. 采集循环据 completed 跳过已完成页，只补未完成的
todo = [p for p in ("page-1", "page-2", "page-3", "page-4")
        if p not in r1["completed"]]
print("待补采:", todo)
for p in todo:
    mark("task-a", p)

# 5. 重复恢复幂等：日志未变，返回同一份报告
r2 = recover("task-a")
r3 = recover("task-a")
print("幂等:", r2 == r3, "| 位点:", r3["position"])

# 6. 已完成页重复 mark 不落重复记录
print("重复 mark:", mark("task-a", "page-2"))

# 7. 模拟恢复过程被中断：再次恢复从同一位置继续
mark("task-b", "page-1")
mark("task-b", "page-2")
mark("task-b", "page-3")


def boom(**kw):
    raise KeyboardInterrupt


recover_mod.PROGRESS_HOOK = boom
r4 = recover("task-b")
recover_mod.PROGRESS_HOOK = None
print("中断于 offset", r4["end"], "| interrupted:", r4["interrupted"])
r5 = recover("task-b")
print("再次恢复从 offset", r5["start"], "继续 | completed:", r5["completed"])

# 8. 新批次重跑：位点不属于当前批次，归档旧日志从头开始
r6 = recover("task-a", batch="batch-20261115")
print("批次不符 -> batch_reset:", r6["batch_reset"], "| completed:", r6["completed"])
