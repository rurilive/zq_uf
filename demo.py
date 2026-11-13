import os
import tempfile

from retention import plan

with tempfile.TemporaryDirectory() as tmp:
    open(os.path.join(tmp, "old.txt"), "w").write("x")
    print(plan(tmp, 30))

# TODO: 第二段演示（实现后补）
