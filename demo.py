from metastore import Registry

registry = Registry(
    aliases={"amt": "amount"},
    downstream={"orders": ["daily_report", "billing_etl"]},
)

rows_v1 = [
    {"id": "1", "amt": "10", "note": "first"},
    {"id": "2", "amt": "", "note": "second"},
]
registry.register({"name": "orders", "rows": rows_v1})

rows_v2 = [
    {"id": "1", "amount": "10.5", "currency": "CNY"},
    {"id": "2", "amount": "20.0", "currency": "USD"},
]
print(registry.diff({"name": "orders", "rows": rows_v2}))
registry.register({"name": "orders", "rows": rows_v2})

for record in registry.history("orders"):
    print(record["version"], sorted(record["fields"]))
