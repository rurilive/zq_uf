"""演示两跳文件链：raw.csv -> clean.csv -> report.csv。"""

import json

from filelineage import export_graph, impact, lineage_path, record

# 两个任务各管一跳：任务读一个文件、写下一个文件，边由此派生。
print("任务一:", record({"id": "job-1", "batch": "2026-11-15", "reads": ["raw.csv"], "writes": ["clean.csv"]}))
print("任务二:", record({"id": "job-2", "batch": "2026-11-15", "reads": ["clean.csv"], "writes": ["report.csv"]}))

# 从源文件向下查两跳：clean.csv 在 1 层，report.csv 在 2 层。
result = impact("raw.csv")
print("\n影响面 raw.csv:")
for node in result["nodes"]:
    print(f"  depth={node['depth']} {node['path']} jobs={node['jobs']}")
print("路径:")
for chain in result["paths"]:
    print("  " + " -> ".join(chain))

# 反向溯源，并给出两个文件之间的一条完整派生路径。
print("\n上游 report.csv:", [node["path"] for node in impact("report.csv", direction="upstream")["nodes"]])
print("链路 raw.csv -> report.csv:", lineage_path("raw.csv", "report.csv")["path"])

# 整图可导出为 JSON，节点是文件，边带读写模式和批次。
print("\n导出图:")
print(json.dumps(export_graph(), ensure_ascii=False, indent=2))
