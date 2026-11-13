import os
import tempfile

from partitiongc import plan

with tempfile.TemporaryDirectory() as tmp:
    os.makedirs(os.path.join(tmp, "2020-01-01"))
    print(plan(tmp, keep_days=30))

# TODO: 第二段演示（实现后补）
