from samplededupe import ingest, report

print(ingest({"name": "locker-a.bin", "digest": "aa11", "family": "ransom", "size": 2048}))
print(ingest({"name": "locker-b.bin", "digest": "aa11", "family": "ransom", "size": 2048}))
print(ingest({"name": "locker-c.bin", "digest": "aa12", "family": "ransom", "size": 2049}))
print(report())

# TODO: 第二段演示（实现后补）
