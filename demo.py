import os
import tempfile

from fileindex import changed, index

root = tempfile.mkdtemp()
path = os.path.join(root, "a.parquet")
open(path, "w").write("AAA")
print(index(root))
print(changed(path))

# TODO: 第二段演示（实现后补）