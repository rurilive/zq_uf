import os
import tempfile

from filededup import add

root = tempfile.mkdtemp()
for name in ("a.txt", "b.txt"):
    path = os.path.join(root, name)
    open(path, "w").write("same")
    print(add(path, os.path.join(root, "store")))

# TODO: 第二段演示（实现后补）
