import os
import tempfile

from csvexport import export

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "out.csv")
    export([["单号", "金额"], ["A001", "10.00"]], path)
    print(open(path).read())

# TODO: 第二段演示（实现后补）
