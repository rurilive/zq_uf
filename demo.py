import json
import os
import tempfile

from schemavolve import SchemaMismatch, read

SCHEMA = ["id", "name", "amount"]
path = os.path.join(tempfile.mkdtemp(), "v1.jsonl")
open(path, "w", encoding="utf-8").write(json.dumps({"id": 1, "name": "键盘", "amount": 299}) + "\n")
print(read(path, SCHEMA))

path2 = os.path.join(os.path.dirname(path), "v2.jsonl")
open(path2, "w", encoding="utf-8").write(json.dumps({"id": 2, "name": "鼠标", "amount": 99, "note": "x"}) + "\n")
try:
    read(path2, SCHEMA)
except SchemaMismatch as exc:
    print("拒绝", exc)

# TODO: 第二段演示（实现后补）
