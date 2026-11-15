"""taskresume 一致性检查点演示：模拟写一半崩溃后的恢复与回退。"""

import os
import tempfile

from taskresume import configure, resume, save

checkpoint_dir = tempfile.mkdtemp(prefix="taskresume-demo-")
configure(checkpoint_dir, keep_versions=3)
print(f"检查点目录: {checkpoint_dir}\n")

task = "demo-job"

# 1) 正常推进：页面入库后保存位点（先落盘再确认）
for page in (1, 2, 3):
    ack = save(task, {"page": page})
    print(f"save page={page} -> durable={ack['durable']} "
          f"version={ack['version']} position={ack['position']}")

state = resume(task)
print(f"resume -> position={state['position']} start={state['start']} "
      f"skipped={state['skipped']} rolled_back={state['rolled_back']}\n")

# 2) 模拟崩溃 A：检查点写一半进程被杀，只留下临时文件，没有原子改名
from urllib.parse import quote

task_dir = os.path.join(checkpoint_dir, quote(task, safe=""))
tmp_path = os.path.join(task_dir, "checkpoint-0000000099-crashed.tmp")
with open(tmp_path, "wb") as f:
    f.write(b'{"format": 1, "half": true')
print(f"模拟崩溃：写一半残留临时文件 {os.path.basename(tmp_path)}")
state = resume(task)
print(f"resume -> position={state['position']} "
      f"recovered={state['recovered']} rolled_back={state['rolled_back']}"
      "（临时文件不参与恢复）")

ack = save(task, {"page": 4})
print(f"再次 save -> 遗留临时文件已清理，新版本 version={ack['version']}\n")

# 3) 模拟崩溃 B：最新检查点文件被截断（改名后写到一半）
newest = max(
    os.path.join(task_dir, n)
    for n in os.listdir(task_dir)
    if n.startswith("checkpoint-") and n.endswith(".json")
)
with open(newest, "wb") as f:
    f.truncate(11)
print(f"模拟崩溃：最新检查点截断损坏 {os.path.basename(newest)}")

state = resume(task)
print(f"resume -> position={state['position']} start={state['start']} "
      f"skipped={state['skipped']} rolled_back={state['rolled_back']} "
      f"version={state['version']}（回退到上一有效点）")
again = resume(task)
print(f"重复 resume -> 同一结果: {again == state}\n")

# 4) 任务隔离：另一个采集任务有自己的目录和位点
save("other-job", {"page": 9})
print("任务目录隔离:", sorted(os.listdir(checkpoint_dir)))
print("other-job:", resume("other-job"))
