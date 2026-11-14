from fieldmerge import merge, reset

reset()

# 同一行 order-7 的三个来源：ERP 最可信但较旧，CRM 较新但可信度低，
# WMS 与 ERP 同分，演示同分冲突与逐字段候选。
result = merge("order-7", {
    "erp": {
        "$confidence": 0.9,
        "$timestamp": 1000.0,
        "fields": {"amount": 100.0, "status": "paid"},
    },
    "crm": {
        "$confidence": 0.4,
        "$timestamp": 2000.0,
        "fields": {"amount": 120.0, "owner": "Ada"},
    },
    "wms": {
        "$confidence": 0.9,
        "$timestamp": 1000.0,
        "fields": {"amount": 100.0, "warehouse": "HK-1"},
    },
})

print("主记录:", dict(result))
print("胜出来源:", result.winning_source, "权重:", round(result.weight, 4))
print("来源打分:")
for source, info in result.sources.items():
    print(
        " ", source,
        "confidence=" + str(info["confidence"]),
        "decay=" + str(round(info["decay"], 4)),
        "weight=" + str(round(info["weight"], 4)),
    )
print("冲突:")
for conflict in result.conflicts:
    print(" ", conflict)
print("逐字段候选:")
for field, candidates in result.candidates.items():
    print(" ", field, [(c["source"], c["value"], round(c["weight"], 4))
                       for c in candidates])
print("规则命中:", [r["rule"] for r in result.rules])
