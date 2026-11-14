import os
import tempfile

from partcompact import compact, query

root = tempfile.mkdtemp()
open(os.path.join(root, "p1.jsonl"), "w").write('{"id": 1}\n')
compact(root, [{"id": 1}, {"id": 2}])
print(query(root))

# TODO: 第二段演示（实现后补）
