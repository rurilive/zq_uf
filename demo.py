"""两批交叉数据的并发幂等填报演示。

批次 A、B 共享同一个回执存储和业务键协调器，在两个线程里并发提交；
两批存在相同业务键，协调器保证同一键只有一个在跑，另一个进等待队列，
拿到结果后按回执核对为跳过，不会开出重复单据。
"""
import threading
import time

from formfill import (
    DialogBlocked,
    KeyCoordinator,
    ReceiptStore,
    submit_batch,
)
from formfill.template import Template

# 模板声明：业务键 = 公司:月份:单号，中文列名映射，斜杠日期，千分位金额。
TEMPLATE = Template(
    key_fields=("company", "month", "id"),
    date_input_formats=("%Y/%m/%d",),
    amount_precision=2,
    amount_rounding="half_up",
    timezone="Asia/Hong_Kong",
    field_map={"date": "报销日期", "amount": "金额(元)", "note": "说明"},
)

BATCH_A = [
    {"company": "acme", "month": "2026-03", "id": 1,
     "报销日期": "2026/03/01", "金额(元)": "1,200.345", "说明": "差旅"},
    {"company": "acme", "month": "2026-03", "id": 2,
     "报销日期": "2026/03/02", "金额(元)": "88.00", "说明": "打车"},
    {"company": "acme", "month": "2026-03", "id": 3,
     "报销日期": "2026/03/03", "金额(元)": "5.5", "说明": "餐费"},
]

# 与 A 交叉：键 acme:2026-03:2 重复，另有新单和一条坏金额。
BATCH_B = [
    {"company": "acme", "month": "2026-03", "id": 2,
     "报销日期": "2026/03/02", "金额(元)": "88.00", "说明": "打车"},
    {"company": "acme", "month": "2026-03", "id": 4,
     "报销日期": "2026/03/04", "金额(元)": "333.7", "说明": "住宿"},
    {"company": "acme", "month": "2026-03", "id": 5,
     "报销日期": "2026/03/05", "金额(元)": "不是数字", "说明": "招待"},
]


def make_submit_one():
    confirmed = set()

    def submit_one(row):
        time.sleep(0.12)  # 模拟页面/接口耗时，放大并发交叉窗口
        key = row["key"]
        # 4 号单首次遇到确认弹窗，确认后重放才成功。
        if key.endswith(":4") and key not in confirmed:
            confirmed.add(key)
            raise DialogBlocked("confirm", "请确认住宿金额")
        # 3 号单被校验规则拒绝。
        if key.endswith(":3"):
            return {
                "dialog": {
                    "type": "validation",
                    "message": "餐费超出日额度",
                    "fields": [{"field": "金额(元)", "reason": "超出日额度"}],
                }
            }
        return {"status": "ok", "receipt_id": f"R-{key}"}

    return submit_one


def print_report(title, result):
    print(f"\n== {title} {result.counts()} ==")
    for bucket in ("success", "skipped", "failed", "manual", "preview"):
        for receipt in getattr(result, bucket):
            extra = f" fields={receipt.fields}" if receipt.fields else ""
            print(f"  [{bucket}] {receipt.business_key} {receipt.status} {receipt.message}{extra}")


def main():
    store = ReceiptStore()
    coordinator = KeyCoordinator()

    print("== 干跑预览（不提交、不写回执）==")
    preview = submit_batch(
        BATCH_A + BATCH_B, workers=4, template=TEMPLATE,
        store=store, coordinator=coordinator, dry_run=True,
    )
    for receipt in preview.preview:
        print(f"  [preview] {receipt.business_key}")
    for receipt in preview.failed:
        print(f"  [failed ] {receipt.business_key} {receipt.message}")

    results = {}

    def run(name, batch_rows):
        results[name] = submit_batch(
            batch_rows, workers=3, submit_one=make_submit_one(),
            template=TEMPLATE, store=store, coordinator=coordinator,
        )

    thread_a = threading.Thread(target=run, args=("A", BATCH_A))
    thread_b = threading.Thread(target=run, args=("B", BATCH_B))
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()
    print_report("批次 A", results["A"])
    print_report("批次 B", results["B"])
    print(f"\n协调器等待统计: {coordinator.stats()}")

    # 整批重试：成功单按回执跳过，失败单（3、5）重放后仍是失败。
    retry = submit_batch(
        BATCH_A + BATCH_B, workers=4,
        submit_one=make_submit_one(),
        template=TEMPLATE, store=store, coordinator=coordinator,
    )
    print_report("重试批次", retry)


if __name__ == "__main__":
    main()
