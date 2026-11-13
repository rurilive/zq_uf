import os
import tempfile

from auditlog import record, replay

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "audit.jsonl")
    record({"ok": 2, "failed": 1}, path)
    print(replay(path))

# TODO: 第二段演示（实现后补）
