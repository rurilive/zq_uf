import json
import os
import tempfile

from harvestcfg import load

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "site.json")
    open(path, "w", encoding="utf-8").write(json.dumps({"rate": 2, "workers": 4}))
    print(load(path))

# TODO: 第二段演示（实现后补）
