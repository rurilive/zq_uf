"""分区统计，当前全量扫描。"""
import os


def stats(dataset):
    """每次现场数文件。"""
    if not os.path.isdir(dataset):
        return {"files": 0, "rows": 0}
    files = [n for n in os.listdir(dataset) if os.path.isfile(os.path.join(dataset, n))]
    return {"files": len(files), "rows": 0}


def prune(filters):
    """没有裁剪，返回全部。"""
    return {"scan": 0, "skip": 0}
