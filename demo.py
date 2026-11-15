from filelineage import impact, record

print(record({"id": "job-1", "reads": ["raw.csv"], "writes": ["clean.csv"]}))
print(record({"id": "job-2", "reads": ["clean.csv"], "writes": ["report.csv"]}))
print(impact("raw.csv"))

# TODO: 第二段演示（实现后补）
