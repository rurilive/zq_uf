import os
import tempfile

from filerouter import route

with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "a.log")
    open(src, "w").write("x")
    print(route(src, os.path.join(tmp, "out")))

# TODO: 第二段演示（实现后补）
