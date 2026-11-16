"""logmask 演示：字段级脱敏、未登记拒绝、规则版本与回滚、关联标识。

运行：python3 demo.py
"""

from logmask import (
    PARTIAL,
    PLAIN,
    UnregisteredFieldError,
    configure,
    mask,
    report,
    rollback,
)

# 规则按服务登记：同名字段在不同服务可以有不同处理方式。
configure(
    "order",
    {
        "phone": {"mode": PARTIAL, "keep_head": 3, "keep_tail": 4},  # 保格式
        "id_card": "full",                                            # 全遮蔽
        "order_no": PLAIN,                                            # 业务字段不动
        "note": PLAIN,
    },
    note="initial order rules",
)
configure("pay", {"phone": "full", "card_no": "full"})

RECORD = {
    "service": "order",
    "phone": "13800001111",
    "id_card": "110101199003071234",
    "order_no": "2026010112345678",
    "note": "user called back",
}

# 第一段：按字段和用途区分处理，trace_id 可与原始请求关联。
result = mask(RECORD)
print("masked record:", result["record"])
print("handled fields:", result["masked"])
print("trace id:", result["trace_id"], "rule version:", result["version"])

# 第二段：新字段未登记 -> 写入被拒绝，字段名直接指出，事件计入报表。
new_field_record = dict(RECORD, device="13900002222")
try:
    mask(new_field_record)
except UnregisteredFieldError as exc:
    print("rejected write:", exc)

# 规则变更只对新增日志生效；trace_id 不随规则变化，回滚按服务进行。
configure("order", {"phone": "full", "id_card": "full", "order_no": PLAIN,
                    "note": PLAIN}, note="harden phone to full")
hardened = mask(RECORD)
print("after rule change:", hardened["record"], "version:", hardened["version"])
print("trace id unchanged:", hardened["trace_id"] == result["trace_id"])

rollback("order")
rolled_back = mask(RECORD)
print("after rollback:", rolled_back["record"], "version:",
      rolled_back["version"])

print("report:", report())
