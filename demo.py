import os
import tempfile

from crawljournal import append, recover

path = os.path.join(tempfile.mkdtemp(), "journal.log")
append({"id": 1}, path)
append({"id": 2}, path)
print(recover(path))

# TODO: 第二段演示（实现后补）
