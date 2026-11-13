from batchrerun import register, rerun

register({"id": "b1", "tasks": [{"name": "a", "ok": True}, {"name": "b", "ok": False}]})
print(rerun("b1"))
print(rerun("b1"))

# TODO: 第二段演示（实现后补）