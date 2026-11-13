import os
import tempfile

from csvrepair import repair

path = os.path.join(tempfile.mkdtemp(), "dirty.csv")
open(path, "w", encoding="utf-8").write("1,keyboard\n2,\"mouse,black\",99\n")
print(repair(path, ["id", "name", "price"])["rows"])

# 第二段：列数不齐、引号未闭合、嵌入换行、无法修复的脏文件
messy = os.path.join(tempfile.mkdtemp(), "messy.csv")
open(messy, "w", encoding="utf-8").write(
    "1,keyboard\n"
    "2,\"mouse,black\",99\n"
    "3,\"key board,49\n"
    "4,\"lap\ntop\",1999\n"
    "5,a,b,c,d\n"
)
result = repair(messy, ["id", "name", "price"])
print(result["repaired"])
print(result["actions"])
print(result["count"], result["checksum"])
print(result["report_path"])
