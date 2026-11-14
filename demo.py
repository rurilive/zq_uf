import os
import tempfile

import json

from auditchain import append, export_report, rotate, verify

path = os.path.join(tempfile.mkdtemp(), "audit.log")
append({"id": 1, "action": "update"}, path)
print(verify(path=path))

# 第二段演示：轮转后跨文件验证
append({"id": 2, "action": "update"}, path)
append({"id": 3, "action": "update"}, path)
rotate(path)
append({"id": 4, "action": "rotate-append"}, path)
print(verify(path=path))

# 改一条记录再验证：应报断链位置与原因
with open(path, encoding="utf-8") as handle:
    lines = handle.readlines()
record = json.loads(lines[0])
record["event"]["action"] = "delete"
lines[0] = json.dumps(record, ensure_ascii=False) + "\n"
with open(path, "w", encoding="utf-8") as handle:
    handle.writelines(lines)
result = verify(path=path)
print(result)
print(export_report(result, os.path.join(os.path.dirname(path), "report.json")))
