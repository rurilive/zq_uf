import json
import os
import tempfile

from extractconf import apply, load

PAGE = "标题：测试文章 作者：张三 阅读：128"
CONF = {"title": r"标题：(\S+)", "author": r"作者：(\S+)", "views": r"阅读：(\d+)"}

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "conf.json")
    open(path, "w", encoding="utf-8").write(json.dumps(CONF))
    conf = load(path)
    print(apply(PAGE, conf))

# TODO: 第二段演示（实现后补）
