from crawlverify import rollback, verify

rows = [{"id": 1, "title": "ok"}, {"id": 2}]
print(verify(rows, {"required": ["id", "title"]}))
print(rollback("batch-1"))

# TODO: 第二段演示（实现后补）
