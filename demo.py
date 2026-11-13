from fieldlineage import record, reset, trace, trace_field

reset()
record({"job": "j1", "inputs": ["raw.orders"], "outputs": ["dwd.orders"],
        "columns": [{"from": "raw.orders.amount", "to": "dwd.orders.total"}]})
print(trace("dwd.orders"))

# 第二段演示：带别名的 SQL、聚合、多跳与字段改名
record({"job": "j2", "inputs": ["raw.orders"], "outputs": ["dwd.order_daily"],
        "sql": "insert into dwd.order_daily "
               "select user_id, sum(amount) as total_amt, count(*) as order_cnt "
               "from raw.orders group by user_id"})
record({"job": "j3", "inputs": ["dwd.order_daily"], "outputs": ["dm.user_stats"],
        "sql": "insert into dm.user_stats "
               "select user_id as uid, total_amt as lifetime_value from dwd.order_daily"})
record({"job": "j4", "inputs": [], "outputs": [],
        "renames": [{"from": "dm.user_stats.lifetime_value", "to": "dm.user_stats.ltv"}]})
print(trace_field("dm.user_stats", "ltv"))
print(trace("dm.user_stats", "lifetime_value"))  # 改名后旧名经别名历史仍可追溯

