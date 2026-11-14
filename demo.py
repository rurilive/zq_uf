from schemadrift import diff, snapshot

print(diff(snapshot({"columns": ["id"]}), snapshot({"columns": ["id", "note"]})))

# TODO: 第二段演示（实现后补）
