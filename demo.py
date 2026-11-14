import tempfile

from sessionvault import load, save

root = tempfile.mkdtemp()
path = save("acc-1", {"cookie": "sid=abc"}, root=root)
print(path)
print(load("acc-1", root=root))

# TODO: 第二段演示（实现后补）
