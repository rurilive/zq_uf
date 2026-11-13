import json
import os
import tempfile

from csvrepair import repair

SCHEMA = ["id", "name", "price"]

# 第一段：旧契约演示，rows保持朴素切分（与tests断言一致）
path = os.path.join(tempfile.mkdtemp(), "dirty.csv")
open(path, "w", encoding="utf-8").write("1,keyboard\n2,\"mouse,black\",99\n")
print(repair(path, SCHEMA)["rows"])

# 第二段：修一份集齐四类问题的脏文件
dirty = (
    "1,keyboard,299\n"
    "2,\"mouse,black\",99\n"
    "3\n"
    "4,\"wireless\n"
    "mouse\",10\n"
    "5,\"unclosed,1\n"
    "6,a,b,c,d\n"
)
path2 = os.path.join(tempfile.mkdtemp(), "messy.csv")
open(path2, "w", encoding="utf-8").write(dirty)
result = repair(path2, SCHEMA)
print("repaired rows:")
for row in result["repaired"]:
    print(" ", row)
report = json.load(open(result["report_path"], encoding="utf-8"))
print("actions:")
for entry in report["actions"]:
    print(" ", entry["line"], entry["action"], repr(entry["raw"]))
print("output_rows:", report["output_rows"], "sha256:", report["sha256"][:16])
print("report:", result["report_path"])
