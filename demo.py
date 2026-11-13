import os
import tempfile

from shardwrite import read, write

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "rows.jsonl")
    print("写入", write([{"id": 1, "date": "2026-03-01"}, {"id": 2, "date": "2026-03-01"}], path))
    print("读回", read(path))
    print("按日期", read(path, date="2026-03-01"))

# TODO: 第二段演示（实现后补）
