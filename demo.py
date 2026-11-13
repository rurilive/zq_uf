import os
import tempfile

from sheetload import load

SHEET = "单号,金额,日期\nA001,10.00,2026-03-01\nA002,20.00,2026-03-02\n"

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "sheet.csv")
    open(path, "w", encoding="utf-8").write(SHEET)
    for row in load(path, ["单号", "金额", "日期"]):
        print(row)

# 第二段：带校验的导入（脏表格：空行、合并单元格、公式、类型错误、重复键）
DIRTY = (
    "单号,项目,金额,日期,状态\n"
    "A001,运费,=10+20,2026-03-01,待采\n"
    "\n"
    ",装卸,40,2026-03-02,待采\n"
    "A002,运费,abc,2026-03-03,待采\n"
    "A003,运费,60,2026-13-01,待采\n"
    "A001,运费,70,2026-03-04,待采\n"
    "A004,,80,2026-03-05,已采\n"
)

SCHEMA = {
    "columns": [
        {"name": "单号", "required": True, "fill_down": True},
        {"name": "项目", "required": True},
        {"name": "金额", "type": "decimal", "range": [0, 10000]},
        {"name": "日期", "type": "date"},
        {"name": "状态", "enum": ["待采", "已采"]},
    ],
    "key": ["单号", "项目"],
    "on_conflict": "skip",
    "formulas": "value",
    "batch_size": 2,
}

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "dirty.csv")
    open(path, "w", encoding="utf-8").write(DIRTY)
    dry = load(path, dict(SCHEMA, dry_run=True))
    print("干跑: 有效 %d 行, 错误 %d 条, 空行 %d, 冲突 %d, 提交 %d"
          % (len(dry.rows), len(dry.errors), dry.blank_rows,
             len(dry.conflicts), dry.committed))
    for err in dry.errors:
        print("  第%d行 [%s] %s" % (err.line, err.column, err.reason))
    done = load(path, SCHEMA)
    print("正式: 提交 %d 行, 分 %d 批" % (done.committed, len(done.batches)))
    for row in done.rows:
        print(row)
