"""演示多代际检查点：保留策略、写到一半崩溃、损坏回退。"""

import os
import tempfile

import ckptgen.checkpoint as cp
from ckptgen import (
    current_generation,
    fallback_records,
    load,
    retained_generations,
    save,
)

root = tempfile.mkdtemp(prefix="ckptgen-demo-")
task = "task-1"


def section(title):
    print("\n== " + title + " ==")


section("连续保存 6 次，只保留最近 3 代")
for cursor in range(1, 7):
    result = save(task, {"cursor": cursor}, root=root, keep=3)
print("当前代际:", current_generation(task, root=root))
print("保留列表:", retained_generations(task, root=root))
print("最后一次 save 返回:", result)
print("load(task, 5):", load(task, 5, root=root))

section("模拟写到一半崩溃（原子改名前抛错）")
task_dir = os.path.join(root, task)
original_commit = cp._commit


def crash_commit(tmp_path, final_path):
    raise RuntimeError("模拟进程崩溃：临时文件尚未改名")


cp._commit = crash_commit
try:
    try:
        save(task, {"cursor": 999}, root=root, keep=3)
    except RuntimeError as error:
        print("捕获崩溃:", error)
finally:
    cp._commit = original_commit

leftover = [name for name in os.listdir(task_dir) if name.endswith(".tmp")]
print("异常被捕获后临时文件已清理:", leftover)
stray = cp._gen_path(task, current_generation(task, root=root) + 1, root) + ".tmp"
with open(stray, "w", encoding="utf-8") as handle:
    handle.write("{模拟硬崩溃残留的半截文件")
print("模拟硬崩溃残留临时文件:", os.path.basename(stray))
print("最新可读代际仍是:", load(task, root=root))
save(task, {"cursor": 7}, root=root, keep=3)
print("恢复后残留临时文件:",
      [name for name in os.listdir(task_dir) if name.endswith(".tmp")])
print("当前代际:", current_generation(task, root=root),
      "保留列表:", retained_generations(task, root=root))

section("损坏最新代际，load 自动回退到上一代")
latest = current_generation(task, root=root)
with open(cp._gen_path(task, latest, root), "w", encoding="utf-8") as handle:
    handle.write("{not-valid-json")
print("损坏代际:", latest, "后 load 得到:", load(task, root=root))
print("回退记录:", fallback_records(task))
