import json
import os
import tempfile

from partquery import ROOT, query

os.chdir(tempfile.mkdtemp())
os.makedirs(os.path.join(ROOT, "orders"))
open(os.path.join(ROOT, "orders", "dt=2026-01-01.jsonl"), "w", encoding="utf-8").write(
    json.dumps({"city": "sh", "amount": 3}) + "\n")
print(query("orders", {"city": "sh"}))

# TODO: 第二段演示（实现后补）
