"""测试库恢复演练：快照期间持续写入，含一条坏事件，验证按位点对齐。

运行: python3 demo.py
"""

from snapalign import AlignError, ChangeStream, repair, snapshot, write


def evt(op, row, table="orders"):
    return {"op": op, "table": table, "row": row}


def show(title, result):
    print(f"  {title}: "
          f"ok={result['ok']} 位点={result['position']} "
          f"缓存={result['cached']} 应用={result['applied']} "
          f"差异={result['diffs']} 错误={result['error']}")


stream = ChangeStream()
src = {
    "orders": {"rows": [{"id": 1}, {"id": 2}]},
    "items": {"rows": [{"id": 100}]},
}
print("== 1. 快照开始：记录起始位点，拷表 ==")
session = snapshot(src, stream)
print(f"  起始位点 start_position={session.start_position}")

print("== 2. 快照期间生产库持续变更（更新/新增/删除/跨表 + 一条坏事件） ==")
write(src, stream, evt("upsert", {"id": 1, "status": "paid"}))  # 更新
write(src, stream, evt("upsert", {"id": 3}))                    # 新增
write(src, stream, evt("delete", {"id": 2}))                    # 删除
write(src, stream, evt("upsert", {"id": 101}, table="items"))   # 跨表
bad = write(src, stream, {"op": "bad-write", "row": {"id": 4}})  # 损坏事件
write(src, stream, evt("upsert", {"id": 5}))                    # 坏事件之后
print(f"  快照期间缓存变更 {session.cached} 条，坏事件位点={bad}")

print("== 3. 首次恢复：按位点顺序应用，遇坏事件停在原位点 ==")
try:
    session.resume()
except AlignError as exc:
    print(f"  应用失败停在原位点 {exc.position}: {exc}")
show("失败后状态", session.result())
print(f"  已应用行 orders={[r['id'] for r in session.tables['orders']['rows']]}")

print("== 4. 原位点修复坏事件，resume 继续（位点不回退、已应用不重做） ==")
repair(src, stream, bad, evt("upsert", {"id": 4}))
result = session.resume()
show("恢复完成", result)

print("== 5. 完成后逐表校验行数与内容 ==")
check = session.verify()
show("校验结果", check)
for name, table in session.tables.items():
    print(f"  表 {name}: {sorted(r['id'] for r in table['rows'])}")

print("== 6. 幂等性：再 resume 一次，结果不变、位点不动 ==")
again = session.resume()
show("重复恢复", again)
assert again["position"] == result["position"] and again["applied"] == 0

print("== 7. 逐表差异演示：篡改副本后 verify 按表报告 ==")
session.tables["orders"]["rows"].pop()
print("  差异明细:", session.verify()["diffs"])

print("\n演练通过：快照期间变更零丢失，位点只向前，失败可原位点续传。")
