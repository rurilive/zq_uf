import os
import tempfile

from compact import compact

with tempfile.TemporaryDirectory() as tmp:
    open(os.path.join(tmp, "a.txt"), "w").write("1")
    open(os.path.join(tmp, "b.txt"), "w").write("2")
    print(compact(tmp))

# 第二段：大小不一的文件，按大小阈值和单批上限合并
with tempfile.TemporaryDirectory() as tmp:
    for i in range(6):
        rows = "\n".join("row-%d-%d" % (i, j) for j in range(i * 10))
        open(os.path.join(tmp, "part-%d.txt" % i), "w").write(rows)
    open(os.path.join(tmp, "big.txt"), "w").write("x" * 4096)
    policy = {"max_file_size": 1024, "max_batch_size": 300, "min_age_seconds": 0}
    print(compact(tmp, policy))
    print(sorted(os.listdir(tmp)))
