"""三系统对账演示：ERP（元）、支付渠道（分）、CRM（状态大小写不一）。"""
from multisource import reconcile

ERP = [
    {"order_id": "A1", "amount": 100.0, "status": "PAID"},
    {"order_id": "A2", "amount": 200.0, "status": "PAID"},
    {"order_id": "A3", "amount": 300.0, "status": "OPEN"},
]
PAYMENT = [  # 金额单位是分；A2 重复且金额差 0.5 元；A4 只有支付有
    {"order_id": "A1", "amount": 10000, "status": "paid"},
    {"order_id": "A2", "amount": 20050, "status": "paid"},
    {"order_id": "A2", "amount": 20050, "status": "paid"},
    {"order_id": "A4", "amount": 40000, "status": "paid"},
]
CRM = [
    {"order_id": "A1", "amount": 100.0, "status": "Paid"},
    {"order_id": "A2", "amount": 200.0, "status": "PAID"},
    {"order_id": "A3", "amount": 300.0, "status": "CANCELLED"},
]

CONFIG = {
    "mode": "multi",
    "key": "order_id",
    "source_names": ["erp", "payment", "crm"],
    "tolerance": 0.01,
    "conversions": [
        {"field": "amount", "sources": ["payment"], "op": "divide", "factor": 100},
        {"field": "status", "op": "lower"},
    ],
}


def main():
    result = reconcile([ERP, PAYMENT, CRM], CONFIG)
    report = result["report"]
    print(f"对账模式: {result['mode']}  对齐键: {result['key']}  容差: {CONFIG['tolerance']}")
    print(f"来源: {', '.join(result['sources'])}")
    print(f"差异总数: {report['total']}")
    by_type = ", ".join(f"{k}={report['by_type'][k]}" for k in sorted(report["by_type"]))
    print(f"按类型: {by_type}")
    by_source = ", ".join(f"{n}={report['by_source'][n]['total']}" for n in result["sources"])
    print(f"按来源归因: {by_source}")
    print("--- 差异明细（可下钻） ---")
    for diff in result["diffs"]:
        blamed = ", ".join(diff["attribution"]["sources"])
        if diff["kind"] == "duplicate":
            print(f"[duplicate] order_id={diff['key']} 来源={diff['source']} 出现 {diff['count']} 次")
        elif diff["kind"] == "missing":
            print(f"[missing] order_id={diff['key']} 缺失来源={', '.join(diff['missing_from'])}")
        else:
            values = " ".join(f"{n}={v}" for n, v in diff["values"].items())
            consensus = diff["consensus"] if diff["consensus"] is not None else "无"
            print(f"[value] order_id={diff['key']} 字段={diff['field']} {values} 共识={consensus} 归因={blamed}")


if __name__ == "__main__":
    main()
