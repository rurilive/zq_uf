import os
import tempfile

from crawljournal import append, recover
from crawljournal.journal import _encode_frame

path = os.path.join(tempfile.mkdtemp(), "journal.log")
append({"id": 1}, path)
append({"id": 2}, path)

# 模拟写一半崩溃：第三条记录只写出半条帧，进程退出
partial = _encode_frame({"id": 3}, 3)
with open(path, "a", encoding="utf-8") as handle:
    handle.write(partial[: len(partial) // 2])

result = recover(path)
print("records:", result)
print("dropped:", result.dropped, "last_seq:", result.last_seq)
