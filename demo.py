from tablettl import apply, archive_manifest, plan, restore, restore_status

partitions = [
    {"name": "p1", "age_days": 40, "rows": [{"id": 1}, {"id": 2}]},
    {"name": "p2", "age_days": 35, "rows": [{"id": 3}]},
    {"name": "p3", "age_days": 33, "rows": [{"id": 4}], "downstream_confirmed": False},
    {"name": "p4", "age_days": 31, "rows": [{"id": 5}]},
    {"name": "p5", "age_days": 2, "rows": [{"id": 6}]},
]

p = plan("orders", partitions, batch_size=2)
print("expire:", [x["name"] for x in p["expire"]])

result = apply(p)
print("archived:", [(a["name"], a["row_count"]) for a in result["archived"]])
print("deleted:", result["deleted"])
print("skipped:", result["skipped"])
print("batches:", [(b["batch"], b["partitions"], b["deleted"], b["status"]) for b in result["batches"]])

manifest = archive_manifest("orders", "p1")
print("manifest:", {"row_count": manifest["row_count"], "checksum": manifest["checksum"][:12]})

print("restore:", [(r["partition"], r["status"], r["verified"]) for r in restore("orders", "p1")])
print("restore_status:", restore_status("orders", "p1"))
