import os
import tempfile

from snapver import Snapshot

with tempfile.TemporaryDirectory() as tmp:
    root = os.path.join(tmp, "data")
    os.makedirs(root)
    open(os.path.join(root, "a.txt"), "w").write("v1")
    snap = Snapshot(root, os.path.join(tmp, "snaps"))
    print(snap.create("s1"))

# TODO: 第二段演示（实现后补）
