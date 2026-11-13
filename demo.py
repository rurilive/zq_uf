"""录制一组脱敏请求，跨文件检索并回放到独立演练库。"""
import json
import os
import tempfile

from trafficmask import find, record, replay

BATCH = "demo-batch-001"

dirpath = tempfile.mkdtemp()
day1 = os.path.join(dirpath, "traffic-2026-11-13.jsonl")
day2 = os.path.join(dirpath, "traffic-2026-11-14.jsonl")
drill = os.path.join(dirpath, "drill.jsonl")

record({"method": "POST", "url": "http://shop/login",
        "headers": {"Cookie": "sid=abcdefgh",
                    "Authorization": "Bearer tok123456789"},
        "body": "phone=13800000000"},
       path=day1, batch=BATCH, mask=True)
record({"method": "GET", "url": "http://shop/order?phone=13912345678",
        "headers": {"Cookie": "sid=xyz99999"}},
       path=day2, batch=BATCH, mask=True)

print("== 脱敏落盘 ==")
with open(day1, encoding="utf-8") as handle:
    print(handle.read().strip())

replayed = replay(day1, drill_path=drill)
print("== 回放重建 ==")
for req in replayed:
    print(json.dumps(req, ensure_ascii=False))

hits = find(BATCH, [day1, day2])
print("== 跨文件检索 ==")
for req in hits:
    print(json.dumps(req, ensure_ascii=False))

print("== 演练库 ==")
print(drill)

assert replayed[0]["method"] == "POST"
assert replayed[0]["headers"]["Cookie"] == "sid=ab****gh"
assert replayed[0]["headers"]["Authorization"] == "Bearer to********89"
assert replayed[0]["body"] == "phone=138******00"
assert [r["method"] for r in hits] == ["POST", "GET"]
assert hits[1]["url"] == "http://shop/order?phone=139******78"
with open(drill, encoding="utf-8") as handle:
    assert len(handle.read().splitlines()) == 1
print("demo ok")

