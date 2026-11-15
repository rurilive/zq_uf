from tablelineage2 import query, record, remove

# 增量记录：逐任务上线
j1 = {"id": "job-1", "time": 1, "edges": [{"src": "ods.a", "dst": "dwd.b"}]}
j2 = {"id": "job-2", "time": 2, "edges": [{"src": "dwd.b", "dst": "dws.c"}]}
print(record(j1))
print(record(j2))

# 幂等：同任务重复上报不新增边
print(record(j2))

# 环路：job-3 会把 dws.c 指回 ods.a，成环被截断并给出路径
j3 = {"id": "job-3", "time": 3, "edges": [{"src": "dws.c", "dst": "ods.a"}]}
print(record(j3))

# 任务改名：同样的边不重复，只换贡献者
print(record({"id": "job-2-renamed", "time": 4,
              "edges": [{"src": "dwd.b", "dst": "dws.c"}]}))

# 查询：按表 / 按时间过滤
print(query(table="dwd.b"))
print(query(since=4))

# 删除任务：按指纹只清理它贡献的边和孤立节点
print(remove(j1))
print(query())
