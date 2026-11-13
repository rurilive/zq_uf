from lineage import Lineage

lineage = Lineage()

# 一组有依赖的批处理任务：抽取 -> 聚合 -> 报表
lineage.record({"name": "extract", "sql": """
    CREATE TEMP TABLE tmp_orders AS
        SELECT * FROM app.orders WHERE dt = '2026-11-13';
    INSERT INTO dwd.orders_clean SELECT * FROM tmp_orders;
"""})
lineage.record({"name": "aggregate", "sql": """
    INSERT INTO dws.daily_sales
    SELECT dt, SUM(amount) FROM dwd.orders_clean GROUP BY dt;
"""})

# 中间表改名，血缘不断
lineage.rename("dws.daily_sales", "dws.daily_sales_v2")
lineage.record({"name": "report",
                "inputs": ["dws.daily_sales_v2"],
                "outputs": ["ads.sales_report"]})

downstream = lineage.impact("app.orders")
print("app.orders 下游影响:", downstream, dict(downstream.levels))

upstream = lineage.impact("ads.sales_report", direction="upstream")
print("ads.sales_report 上游来源:", upstream, dict(upstream.levels))

print("旧名 dws.daily_sales 下游:", lineage.impact("dws.daily_sales"))
print("临时表 tmp_orders 对外血缘:", lineage.impact("tmp_orders"))
print("一跳边:")
for edge in lineage.edges:
    print(" ", edge)
