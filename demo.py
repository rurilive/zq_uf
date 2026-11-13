import os
import tempfile

from tiering import apply, plan

root = tempfile.mkdtemp()
open(os.path.join(root, "a.parquet"), "w").write("x")
result = plan(root)
print(result)
print(apply(result))

# TODO: 第二段演示（实现后补）