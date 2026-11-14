import json

from schemadiff import diff, impact

orders_v1 = {
    "name": "orders",
    "fields": [
        {"name": "id", "type": "int", "primary_key": True, "nullable": False},
        {"name": "amount", "type": "int", "default": 0, "nullable": False},
        {"name": "note", "type": "str"},
    ],
}

orders_v2 = {
    "name": "orders",
    "fields": [
        {"name": "id", "type": "str", "nullable": False},
        {"name": "amount", "type": "decimal", "default": 0, "nullable": False},
        {"name": "channel", "type": "str", "default": "web"},
    ],
}

lineage = {
    "orders.id": ["dm_orders.order_id"],
    "dm_orders.order_id": ["rpt_daily.order_id"],
    "orders.note": ["dm_orders.note"],
}

payload = diff(orders_v1, orders_v2)
print("== 差异项（按优先级排序） ==")
print(json.dumps(payload["items"], ensure_ascii=False, indent=2))

print("== 下游影响 ==")
print(json.dumps(impact(payload, lineage), ensure_ascii=False, indent=2))
