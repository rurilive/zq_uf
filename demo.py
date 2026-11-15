from rowrepair import ingest, replay, fix, progress, export_quarantine, reset

reset()

# 第一批：坏行比例低，坏行隔离、好行入库
r1 = ingest([{"id": 1}, {"id": 2, "ok": False}, {"id": 3}, {"id": 4}])
print("ingest:", r1)

# 修复隔离区里的坏行，按批幂等回放
qid = export_quarantine()[0]["qid"]
fix(qid, {"id": 2})
print("replay:", replay(r1["batch"]))
print("replay again:", replay(r1["batch"]))  # 重复回放不产生重复行

# 第二批：坏行超比例，整批暂停，修复后回放放行
r2 = ingest([{"id": 5}, {"id": 6, "ok": False}])
print("paused:", r2)
qid = [e for e in export_quarantine() if e["batch"] == r2["batch"]][0]["qid"]
fix(qid, {"id": 6})
print("resumed:", replay(r2["batch"]))

print("progress:", progress())
print("quarantine:", export_quarantine())
