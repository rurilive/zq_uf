"""同一份数据，两份 plan：web 默认口径 vs 门店覆盖口径。"""
import json

from etlconf import transform

ROWS = [
    {"order_id": " 1 ", "qty": "2", "price": "9.9", "note": " web "},
    {"order_id": "2", "qty": "", "price": "3.5", "note": "store"},
    {"order_id": "3", "qty": "0", "price": "N/A", "note": "web"},
]

BASE_PLAN = {
    "nulls": ["N/A"],
    "strip": ["order_id", "note"],
    "cast": {"qty": "int", "price": "float"},
    "fill": {"qty": 0, "price": 0.0},
    "rename": {"order_id": "id"},
    "derive": {"amount": "qty * price"},
    "overrides": {
        "store": {"fill": {"qty": -1}},  # 门店：缺失数量记 -1，与真实零值区分
    },
}

web_plan = dict(BASE_PLAN, source="web")
store_plan = dict(BASE_PLAN, source="store")

print("web  :", json.dumps(transform(ROWS, web_plan), ensure_ascii=False))
print("store:", json.dumps(transform(ROWS, store_plan), ensure_ascii=False))
