"""多租户采集执行层，当前所有租户共用一套目录和配置。"""
import json
import os

RESULTS = "results"
_CONFIG = {"workers": 4, "quota": 1000}


def run(tenant, tasks):
    """租户参数只用于打标签，结果写同一个文件。"""
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, "out.json")
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            rows = json.load(handle)
    for task in tasks:
        rows.append({"tenant": tenant, "task": task, "workers": _CONFIG["workers"]})
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False)
    return path


def read_all(path=None):
    path = path or os.path.join(RESULTS, "out.json")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
