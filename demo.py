from formfill import submit_batch

ROWS = [
    {"id": 1, "date": "2026-03-01", "amount": "12.345"},
    {"id": 2, "date": "2026-03-02", "amount": "88.00"},
    {"id": 3, "date": "2026-03-03", "amount": "5.5"},
]


def submit_one(row):
    if row["key"] == "2":
        return {"status": "rejected", "message": "发票号缺失"}
    return {"status": "ok"}


for receipt in submit_batch(ROWS, submit_one):
    print(receipt.business_key, receipt.status, receipt.message)

# TODO: 第二段演示（实现后补）
