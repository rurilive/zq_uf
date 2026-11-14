import os
import tempfile

from partitionstats import stats

root = tempfile.mkdtemp()
open(os.path.join(root, "p1.jsonl"), "w").write("{}\n")
print(stats(root))

# TODO: 第二段演示（实现后补）
