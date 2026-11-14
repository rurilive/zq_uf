import json
import os
import tempfile

from schemavolve import IncompatibleSchema, SchemaMismatch, read

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

# 第二段：版本化schema兼容读取
# v2相对v1：amount改名为price（字符串类型）、删掉name、新增note
SCHEMA_V2 = {
    "fields": {"id": int, "price": float, "note": str},
    "defaults": {"note": "无备注"},
    "renames": {"amount": "price"},
}

path3 = os.path.join(os.path.dirname(path), "v1_for_v2.jsonl")
with open(path3, "w", encoding="utf-8") as handle:
    handle.write(json.dumps({"id": 1, "amount": "299.5", "name": "键盘"}) + "\n")
    handle.write(json.dumps({"id": 2, "amount": "abc", "note": "促销"}) + "\n")
result = read(path3, SCHEMA_V2)
print(result)
print("报告:", result.report)

path4 = os.path.join(os.path.dirname(path), "broken.jsonl")
open(path4, "w", encoding="utf-8").write(json.dumps({"id": 3, "note": "缺price"}) + "\n")
try:
    read(path4, SCHEMA_V2)
except IncompatibleSchema as exc:
    print("不可兼容，整文件拒绝:", exc)
