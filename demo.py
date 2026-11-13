import os
import tempfile

from incbackup import backup

root = tempfile.mkdtemp()
os.makedirs(os.path.join(root, "data"))
open(os.path.join(root, "data", "a.txt"), "w").write("AAA")
print(backup(os.path.join(root, "data"), os.path.join(root, "backups")))
print(backup(os.path.join(root, "data"), os.path.join(root, "backups")))

# TODO: 第二段演示（实现后补）