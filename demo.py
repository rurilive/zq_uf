import tempfile

from ckptgen import load, save

root = tempfile.mkdtemp()
save("task-1", {"cursor": 1}, root=root)
save("task-1", {"cursor": 2}, root=root)
print(load("task-1", 1, root=root))

# TODO: 第二段演示（实现后补）
