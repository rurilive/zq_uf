import datetime
import hashlib
import os
import tempfile

from partitioncatalog import catalog, list_partitions, register

cwd = os.getcwd()
os.chdir(tempfile.mkdtemp())
register({"dataset": "orders", "name": "dt=2026-01-01"})
register({"dataset": "orders", "name": "dt=2026-01-02"})
print(list_partitions("orders"))

# 第二段：跨月分区 + 范围查询 + 变更日志 + 启动修复
first_day = datetime.date(2026, 1, 28)
for i in range(40):  # 2026-01-28 .. 2026-03-08，跨 1/2/3 月
    day = first_day + datetime.timedelta(days=i)
    name = "dt=%s" % day.isoformat()
    register({
        "dataset": "metrics",
        "name": name,
        "start": day.isoformat(),
        "end": day.isoformat(),
        "rows": 1000 + i,
        "checksum": hashlib.sha1(name.encode("utf-8")).hexdigest(),
    })
print(len(list_partitions("metrics")))
feb = list_partitions("metrics", start="2026-02-01", end="2026-02-28")
print(len(feb), feb[0], feb[-1])

# 同一分区重复写入，行数变化 -> 元数据更新并记 update 事件
register({
    "dataset": "metrics",
    "name": "dt=2026-02-14",
    "start": "2026-02-14",
    "end": "2026-02-14",
    "rows": 9999,
    "checksum": hashlib.sha1(b"dt=2026-02-14-v2").hexdigest(),
})

# 模拟服务重启：目录里多了一个没有元数据的分区，init 扫描修复
os.makedirs(os.path.join(catalog.ROOT, "metrics", "dt=2026-03-09"))
repaired = catalog.init()
print("repaired:", repaired)

events = catalog.read_events()
print("events:", len(events), [e["event"] for e in events[-3:]])
os.chdir(cwd)

