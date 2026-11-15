from partlife import (
    Store,
    apply,
    plan,
    query_archive,
    query_cold,
    recover,
    retry_batch,
)

# 试运行：先在 orders 一个数据集上走通分层、冷层查询、回滚重试和恢复
parts = [
    {"id": "2026-11-14", "rows": 1200, "last_access_days": 1},   # 热，保留
    {"id": "2026-10-20", "rows": 900, "last_access_days": 20},   # 转冷层
    {"id": "2026-10-18", "rows": 800, "last_access_days": 25},   # 转冷层
    {"id": "2026-08-01", "rows": 500, "last_access_days": 100},  # 归档后删除
    {"id": "2026-08-02", "rows": 600, "last_access_days": 100},  # 归档后删除
    {"id": "2026-08-03", "rows": 700, "last_access_days": 100},  # 归档后删除
    {"id": "2026-08-04", "rows": 400, "last_access_days": 100, "active_reads": 2},  # 有租约，跳过
]
store = Store()
store.seed("orders", parts)

p = plan({"name": "orders", "partitions": parts})
print("分层:", p["tiers"], "跳过:", p["skipped"])

store.fail_deletes.add("2026-08-03")  # 模拟第二批删除失败
r = apply(p, store)
print("冷层位置:", r["cold_locations"])
print("归档:", [(a["id"], a["location"]) for a in r["archived"]])
print("批次:", [(b["batch"], b["status"]) for b in r["batch_records"]])

store.fail_deletes.clear()
print("重试批次 2:", retry_batch(store, "orders", 2)["status"])

print("冷层范围查询 2026-10-18..2026-10-31:", query_cold(store, "orders", "2026-10-18", "2026-10-31"))
print("归档范围查询 2026-08-01..2026-08-31:", query_archive(store, "orders", "2026-08-01", "2026-08-31"))

back = recover(store, "orders", "2026-08-01", expected_rows=500)
print("恢复并校验行数:", back)
