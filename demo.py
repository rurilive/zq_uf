import os
import tempfile

from replayfail import record, replay

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "failures.jsonl")
    record({"url": "http://site/ok"}, path)
    record({"url": "http://site/bad"}, path)
    print(replay([path], lambda url: url.endswith("ok")))

# TODO: 第二段演示（实现后补）
