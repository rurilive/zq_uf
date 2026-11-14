from tablesync import progress, rollback, snapshot, sync

# 第一批：全量初始数据
r1 = sync("orders", [{"id": 1, "v": 1}, {"id": 2, "v": 2}])
print("batch1:", r1)

# 第二批：id=1 更新 + 源端删除 id=2（墓碑传播）
r2 = sync("orders", [{"id": 1, "v": 3}, {"id": 2, "_deleted": True}])
print("batch2:", r2)
print("snapshot:", snapshot("orders"))
print("progress:", progress("orders"))

# 回滚到水位 1 后重放第二批，结果幂等
print("rollback:", rollback(1, table="orders"))
print("after rollback:", snapshot("orders"))
r2_replay = sync("orders", [{"id": 1, "v": 3}, {"id": 2, "_deleted": True}], watermark=2)
print("replay:", r2_replay)
print("final:", snapshot("orders"))
