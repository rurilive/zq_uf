import tempfile

import ckptgen.checkpoint as checkpoint
from ckptgen import fallbacks, load, save

root = tempfile.mkdtemp()
save("task-1", {"cursor": 1}, root=root)
save("task-1", {"cursor": 2}, root=root)
print(load("task-1", 1, root=root))

# 模拟写到一半崩溃：临时文件已写，原子改名前进程崩溃
original_replace = checkpoint._atomic_replace
def crash(src, dst):
    raise RuntimeError("simulated crash mid-write")
checkpoint._atomic_replace = crash
try:
    save("task-1", {"cursor": 3}, root=root)
except RuntimeError as error:
    print("崩溃:", error)
finally:
    checkpoint._atomic_replace = original_replace
print("崩溃后最新可用:", load("task-1", root=root))

# 继续写入，触发保留清理（默认保留最近 3 代）
for cursor in range(3, 7):
    info = save("task-1", {"cursor": cursor}, root=root)
print("当前代际:", info["generation"], "保留列表:", info["kept"])

# 损坏最新一代，读取自动回退到上一代
with open(info["path"], "w", encoding="utf-8") as handle:
    handle.write("{broken")
print("回退结果:", load("task-1", root=root))
print("回退记录:", fallbacks("task-1"))
