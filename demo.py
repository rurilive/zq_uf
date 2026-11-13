import os
import tempfile

from sheetload import load

SHEET = "单号,金额,日期\nA001,10.00,2026-03-01\nA002,20.00,2026-03-02\n"

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "sheet.csv")
    open(path, "w", encoding="utf-8").write(SHEET)
    for row in load(path, ["单号", "金额", "日期"]):
        print(row)

# TODO: 第二段演示（实现后补）
