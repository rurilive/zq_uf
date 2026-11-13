import json
import os
import tempfile

from harvestcfg import load, watch


def write(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data))


with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "site.json")
    write(path, {"rate": 2, "workers": 4})
    print(load(path))

# 第二段：热更新——改一次站点限速再回滚。
with tempfile.TemporaryDirectory() as tmp:
    site_path = os.path.join(tmp, "site.json")
    write(os.path.join(tmp, "default.json"), {"rate_limit": 1, "concurrency": 2})
    write(site_path, {"rate_limit": 2})

    watcher = watch(tmp)
    print("初始限速:", watcher.resolved["rate_limit"])

    write(site_path, {"rate_limit": 8})
    preview = watcher.check_once()
    print("差异预览:")
    print(preview)
    print("生效后限速:", watcher.resolved["rate_limit"])

    watcher.rollback()
    write(site_path, {"rate_limit": 2})
    watcher.check_once()
    print("回滚后限速:", watcher.resolved["rate_limit"])
    print("版本记录:", [(v["version"], v["status"]) for v in watcher.versions])
