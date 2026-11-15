"""演示：索引损坏后的可校验重建与原子切换。"""
import json
import os
import shutil

import crawlindex
from crawlindex import progress, query, rebuild, update
from crawlindex import index as idx

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".crawlindex-demo")
shutil.rmtree(DATA_DIR, ignore_errors=True)
crawlindex.reset(DATA_DIR)

print("== 初始全量重建 ==")
print(rebuild())
print(query("page:1"))

print("\n== 增量写入 ==")
for i in range(1, 4):
    update("page:%d" % i, {"title": "第 %d 页" % i})
print(query("page:1"))

print("\n== 模拟索引损坏（篡改落盘文件） ==")
with open(os.path.join(DATA_DIR, "index.json"), "w", encoding="utf-8") as f:
    f.write("CORRUPTED{{{")
print("index.json 已损坏；旧索引继续服务:", query("page:2"))

print("\n== 可校验重建：期间可查、增量按版本合并、原子切换 ==")
once = {"done": False}

def hook():
    if not once["done"]:
        once["done"] = True
        print("  重建中查询 page:1 ->", query("page:1")["found"],
              "| 进度:", progress())
        update("page:4", {"title": "重建期间的增量"})
    return False

result = rebuild(
    source=[("page:%d" % i, {"title": "第 %d 页" % i}) for i in range(1, 4)],
    should_stop=hook)
print("重建结果:", result)
print("切换后增量已合并:", query("page:4"))
with open(os.path.join(DATA_DIR, "meta.json"), encoding="utf-8") as f:
    print("落盘元数据:", json.load(f))

print("\n== 模拟切换失败：自动回退旧索引 ==")
orig = idx._write_atomic

def boom(path, obj):
    if path.endswith("index.json"):
        raise OSError("模拟磁盘写失败")
    return orig(path, obj)

idx._write_atomic = boom
failed = rebuild(source=[("page:9", {"title": "不应出现"})])
idx._write_atomic = orig
print("失败结果:", failed)
print("旧索引保留:", query("page:1"), "| 新键不可见:", query("page:9"))
with open(os.path.join(DATA_DIR, "rollback.log"), encoding="utf-8") as f:
    print("回退日志:", f.read().strip())
