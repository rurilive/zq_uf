from quarantine import ingest

try:
    ingest([{"id": 1}, {"id": 2, "bad": True}])
except ValueError as exc:
    print("整批失败", exc)

# TODO: 第二段演示（实现后补）
