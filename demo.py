from syncconflict import merge

print(merge({"id": 1, "price": "1"}, {"id": 1, "price": "2"}))
print(merge({"id": 2, "deleted": True}, {"id": 2, "price": "9"}))

# 两端并发修改同一行：version 相同、内容不同，按策略裁决
local = {"id": 1, "price": "10", "version": 3, "updated_at": 1000, "source": "shop-A"}
remote = {"id": 1, "price": "12", "version": 3, "updated_at": 1001, "source": "shop-B"}

r = merge(local, remote, strategy="local")
print("local-first:", r["price"], r.winner, [c.field for c in r.conflicts])
r = merge(local, remote, strategy="remote")
print("remote-first:", r["price"], r.winner, [c.field for c in r.conflicts])
r = merge(local, remote, strategy="manual", resolver=lambda info: "remote")
print("manual:", r["price"], r.winner, [c.field for c in r.conflicts])

# 墓碑：version 4 的删除不被 version 3 的旧更新复活
tomb = {"id": 2, "deleted": True, "version": 4, "updated_at": 1002, "source": "shop-A"}
stale = {"id": 2, "price": "9", "version": 3, "updated_at": 999, "source": "shop-B"}
r = merge(tomb, stale)
print("tombstone:", r["deleted"], r["version"], r.winner)
