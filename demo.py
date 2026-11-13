import os
import tempfile

from trafficmask import record, replay

path = os.path.join(tempfile.mkdtemp(), "traffic.jsonl")
record({"method": "POST", "url": "http://shop/login", "headers": {"Cookie": "sid=abc"}}, path=path)
print(replay(path))

# TODO: 第二段演示（实现后补）
