from cdcmerge import Merger

merger = Merger()

# 1) 事务原子性：txn=1 的两条变更提交前不可见。
merger.apply([
    {"op": "insert", "key": "a", "value": "v1", "version": 1,
     "txn": 1, "seq": 1},
    {"op": "insert", "key": "b", "value": "v1", "version": 1,
     "txn": 1, "seq": 2},
])
print("事务提交前:", merger.snapshot())
merger.apply([{"op": "commit", "txn": 1}])
print("事务提交后:", merger.snapshot())

# 2) 乱序到达：版本 3 先到，旧版本 2 后到，仍收敛到最高版本。
merger.apply([
    {"op": "update", "key": "a", "value": "v3", "version": 3},
    {"op": "update", "key": "a", "value": "v2", "version": 2},
])
print("乱序收敛后:", merger.snapshot())

# 3) 删除墓碑：晚到的旧版本不能让 a 复活。
merger.apply([{"op": "delete", "key": "a", "version": 4}])
merger.apply([{"op": "update", "key": "a", "value": "stale", "version": 2}])
print("墓碑拦截旧版本后:", merger.snapshot())

# 4) 重复投递：整条事务重放，结果保持幂等。
replayed = [
    {"op": "insert", "key": "c", "value": "v1", "version": 1,
     "txn": 2, "seq": 1},
    {"op": "commit", "txn": 2},
]
merger.apply(replayed)
merger.apply(replayed)
print("重复投递后:", merger.snapshot())
