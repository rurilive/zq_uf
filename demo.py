import os
import tempfile

from snapver import Snapshot

with tempfile.TemporaryDirectory() as tmp:
    root = os.path.join(tmp, "data")
    os.makedirs(root)
    snap = Snapshot(root, os.path.join(tmp, "snaps"))

    open(os.path.join(root, "a.txt"), "w").write("v1")
    print("s1:", snap.create("s1")["created_at"])

    open(os.path.join(root, "a.txt"), "w").write("v2")
    open(os.path.join(root, "b.txt"), "w").write("v2")
    print("s2:", snap.create("s2")["created_at"])

    open(os.path.join(root, "a.txt"), "w").write("v3")
    open(os.path.join(root, "draft.tmp"), "w").write("写到一半")
    meta = snap.create("s3")
    print("s3 文件清单:", sorted(meta["files"]))

    restored = snap.restore("s1")
    print("回滚 s1，恢复文件数:", restored)
    print("a.txt =", open(os.path.join(root, "a.txt")).read())
