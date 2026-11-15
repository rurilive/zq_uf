from rowrepair import ingest, replay

print(ingest([{"id": 1}, {"id": 2, "ok": False}]))
print(ingest([{"id": 3}]))
print(replay("last"))

# TODO: 第二段演示（实现后补）
