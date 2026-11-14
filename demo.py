"""两跳链路演示：raw.orders -> dwd.orders -> dws.order_daily。"""
from rowlineage import propagate, record, reset, trace

reset()

# 第一跳：从 SQL 解析主键映射和过滤条件，生成行级边
record({
    "job": "j1",
    "sql": "INSERT INTO dwd.orders (order_id, amount) "
           "SELECT id, amount FROM raw.orders WHERE id IN (1, 2, 3)",
    "ts": 1000,
})

# 第二跳：keys 给主键映射，rows 给显式行级边
record({
    "job": "j2",
    "inputs": ["dwd.orders"],
    "outputs": ["dws.order_daily"],
    "keys": {"dwd.orders.order_id": "dws.order_daily.order_id"},
    "rows": [("dwd.orders", 1, "dws.order_daily", 1),
             ("dwd.orders", 2, "dws.order_daily", 2)],
    "ts": 2000,
})

print("== 表级 ==")
print(trace("dwd.orders"))

print("== 行级：raw.orders#1 的两跳下游 ==")
print(trace("raw.orders", 1))

print("== 增量传播 ==")
print(propagate("raw.orders", 1, update_id="u-1"))

print("== 重复传播（幂等） ==")
print(propagate("raw.orders", 1, update_id="u-1"))
