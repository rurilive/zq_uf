import os
import tempfile

from auditexport import export

path = os.path.join(tempfile.mkdtemp(), "audit.jsonl")
print(export({"records": [{"id": 1}]}, path))

# TODO: 第二段演示（实现后补）
