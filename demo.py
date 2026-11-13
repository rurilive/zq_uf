import os
import tempfile

from sheetload import load

SHEET = "单号,金额,日期\nA001,10.00,2026-03-01\nA002,20.00,2026-03-02\n"

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "sheet.csv")
    open(path, "w", encoding="utf-8").write(SHEET)
    for row in load(path, ["单号", "金额", "日期"]):
        print(row)

# 第二段：带校验的导入，脏表格含空行、合并单元格、公式、类型错误和重复单号
from decimal import Decimal

DIRTY = (
    "单号,金额,日期,状态,备注\n"
    "A001,10.00,2026-03-01,待处理,首单\n"
    "\n"
    "A002,20.00,2026-03-02,已处理,\n"
    "A002,99.99,2026-03-03,待处理,重复单\n"
    'A003,不是数字,2026-13-40,未知,"=SUM(1,2)"\n'
)

SCHEMA = {
    "fields": [
        {"name": "单号", "required": True},
        {"name": "金额", "type": "decimal", "min": Decimal("0")},
        {"name": "日期", "type": "date"},
        {"name": "状态", "enum": ["待处理", "已处理"]},
        {"name": "备注", "merged": True},
    ],
    "key": "单号",
    "on_conflict": "skip",
    "batch_size": 2,
}

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "订单.csv")
    open(path, "w", encoding="utf-8").write(DIRTY)

    report = load(path, dict(SCHEMA, dry_run=True))
    print("干跑: 可导入 %d 条, 空行 %d 行, 冲突 %d 起" % (
        report["total"], report["skipped_empty"], len(report["conflicts"])))
    for err in report["errors"]:
        print("  第%(row)s行 [%(column)s] %(message)s" % err)

    committed = []
    report = load(path, dict(SCHEMA, commit=committed.extend))
    print("正式导入: %d 条, 分 %d 批提交" % (report["total"], len(report["batches"])))
    for row in committed:
        print(" ", row)
