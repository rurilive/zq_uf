from tablelineage2 import record, remove

print(record({"id": "job-1", "edges": [{"src": "ods.a", "dst": "dwd.b"}]}))
print(remove({"id": "job-1"}))

# TODO: 第二段演示（实现后补）
