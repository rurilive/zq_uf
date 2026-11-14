import json
import tempfile

from partitionmove import artifacts, move, progress, read, rollback, rows_at, seed, write

STATE = tempfile.mkdtemp(prefix="partitionmove-demo-")
print("state_dir:", STATE)
seed("dt=1", [{"id": 1}, {"id": 2}], path="hot", state_dir=STATE)
print("before:", read("dt=1", state_dir=STATE))


def business_write(info):
    """迁移期间的写入：双写并推进位点。"""
    result = write("dt=1", {"id": 3}, state_dir=STATE)
    journal = progress("dt=1", state_dir=STATE)
    print("write:", json.dumps(result, ensure_ascii=False))
    print("journal:", journal["phase"], "cursor=", journal["cursor"])


def fail_switch(info):
    raise RuntimeError("模拟切换失败")


print("\n--- 场景一：切换失败，自动回滚 ---")
result = move(
    "dt=1",
    "cold",
    state_dir=STATE,
    hooks={"before_switch": business_write, "after_switch": fail_switch},
)
print("move:", json.dumps(
    {k: result[k] for k in ("ok", "phase", "phases", "cursor", "rolled_back", "error")},
    ensure_ascii=False,
))
print("rollback:", json.dumps(result["rollback"], ensure_ascii=False))
print("active:", read("dt=1", state_dir=STATE))
print("hot:", rows_at("dt=1", "hot", state_dir=STATE))
print("manifest:", artifacts(state_dir=STATE)["manifest"])

print("\n--- 场景二：迁移成功，之后手动回滚 ---")
result = move("dt=1", "cold", state_dir=STATE)
print("move:", json.dumps(
    {k: result[k] for k in ("ok", "phase", "phases", "cursor", "rolled_back")},
    ensure_ascii=False,
))
print("switch:", json.dumps(result["switch"], ensure_ascii=False))
print("active:", read("dt=1", state_dir=STATE))
rb = rollback(partition="dt=1", state_dir=STATE)
print("rollback:", json.dumps(
    {"ok": rb["ok"], "restored": rb["rolled_back"][0]["restored"],
     "verified": rb["rolled_back"][0]["verified"]},
    ensure_ascii=False,
))
print("active after rollback:", read("dt=1", state_dir=STATE))
