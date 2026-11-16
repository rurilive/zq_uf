from logmask import mask, report

RECORD = {
    "service": "order",
    "phone": "13800001111",
    "id_card": "110101199003071234",
    "order_no": "2026010112345678",
    "note": "user called back",
}

result = mask(RECORD)
print(result)
print(result["record"])
print(report())

# TODO: 第二段演示（实现后补）
