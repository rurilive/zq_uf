import os
import tempfile

from auditchain import append, verify

path = os.path.join(tempfile.mkdtemp(), "audit.log")
append({"id": 1, "action": "update"}, path)
print(verify(path=path))

# TODO: 第二段演示（实现后补）
