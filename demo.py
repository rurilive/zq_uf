import os
import tempfile

from crawljournal import append, recover

path = os.path.join(tempfile.mkdtemp(), "journal.log")
append({"id": 1}, path)
append({"id": 2}, path)
print(recover(path))

# 模拟写一半崩溃：半行没有换行符，进程退出
with open(path, "a", encoding="utf-8") as handle:
    handle.write('{"len": 9, "seq": 3, "checksum": "00000000", "da')

# 恢复：半行被丢弃并截断，输出丢弃条数和最后有效序号
print(recover(path))

# 崩溃恢复后继续写，序号自动续上
append({"id": 3}, path)
print(recover(path))
