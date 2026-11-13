import os
import tempfile

from compact import compact

with tempfile.TemporaryDirectory() as tmp:
    open(os.path.join(tmp, "a.txt"), "w").write("1")
    open(os.path.join(tmp, "b.txt"), "w").write("2")
    print(compact(tmp))

# TODO: 第二段演示（实现后补）
