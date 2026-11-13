from failcluster import record, report

record({"site": "shop", "error": "timeout", "url": "/1"})
record({"site": "news", "error": "timeout", "url": "/2"})
print(report())

# TODO: 第二段演示（实现后补）
