import json

from pipelineview import build, export, impact, load, upstream

# 第一段：原有任务级用法（已有断言锁定）
jobs = [{"name": "extract"}, {"name": "clean", "depends": ["extract"]}]
print(build(jobs)["nodes"])
print(impact("extract"))

# 第二段：任务 + 字段两级视图，运行记录补全实际依赖
JOBS = [
    {"name": "extract",
     "writes": [{"table": "raw_orders", "fields": ["id", "amount"]}]},
    {"name": "clean", "depends": ["extract"],
     "reads": [{"table": "raw_orders", "fields": ["id", "amount"]}],
     "writes": [{"table": "orders", "fields": ["id", "amount"]}]},
    {"name": "report",
     "reads": [{"table": "orders", "fields": ["id", "amount"]}]},
]
RUNS = [
    {"job": "extract", "writes": ["raw_orders"]},
    {"job": "clean", "reads": ["raw_orders"], "writes": ["orders"]},
    {"job": "report", "reads": ["orders"]},
]

view = build(JOBS, runs=RUNS)
print("inferred:", view["inferred"])
print("tables:", view["tables"])
print("fields:", view["fields"])
print("impact(extract):", impact("extract", depth=None))
print("impact(raw_orders.amount):", impact("raw_orders.amount", depth=None))
print("upstream(report):", upstream("report", depth=None))
result = impact("extract", depth=2, paths=True)
print("paths:", json.dumps(result["paths"], ensure_ascii=False))
print("levels:", json.dumps(result["levels"], ensure_ascii=False, sort_keys=True))

# 第三段：导出并可恢复，重启后复现同样的查询结果
data = export()
load(data)
print("restored:", impact("extract", depth=None))

