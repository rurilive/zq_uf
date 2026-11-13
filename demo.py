from batchrerun import batches, history, register, rerun

register({"id": "b1", "tasks": [{"name": "a", "ok": True}, {"name": "b", "ok": False}]})
print(rerun("b1"))
print(rerun("b1"))

# 第二段：半成功批次的分区、历史与统计
print(batches["b1"])
print(batches["last"])
print(history)
