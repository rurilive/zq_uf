from lineage import Lineage

lineage = Lineage()

lineage.record({
    "name": "extract_orders",
    "outputs": ["ods.orders"],
    "sql": """
        CREATE TEMP TABLE tmp_orders AS
        SELECT * FROM ods.orders_src WHERE dt = '2026-11-13';
        INSERT INTO ods.orders SELECT * FROM tmp_orders;
    """,
})
lineage.record({
    "name": "build_user_daily",
    "sql": """
        INSERT INTO dws.user_daily
        SELECT o.user_id, u.region, sum(o.amount) AS total
        FROM ods.orders o
        JOIN ods.users u ON o.user_id = u.user_id
        GROUP BY o.user_id, u.region;
    """,
})
lineage.record({
    "name": "build_region_report",
    "inputs": ["dws.user_daily"],
    "outputs": ["ads.region_report"],
})

# 表改名：ods.users -> ods.users_v2，旧名保留为历史别名
lineage.rename("ods.users", "ods.users_v2")

down = lineage.impact("ods.orders")
print("ods.orders 下游影响:", list(down))
print("按层级:", down.levels)
up = lineage.impact("ads.region_report", direction="upstream")
print("ads.region_report 上游依赖:", list(up))
print("旧名 ods.users 下游:", list(lineage.impact("ods.users")))
print("节点:", lineage.nodes)
print("边:", lineage.edges)
