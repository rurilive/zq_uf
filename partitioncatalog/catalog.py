"""分区目录，当前查询靠遍历目录。"""
import os

ROOT = "warehouse"


def register(partition):
    """只建目录，不写任何元数据。"""
    path = os.path.join(ROOT, partition["dataset"], partition["name"])
    os.makedirs(path, exist_ok=True)
    return path


def list_partitions(dataset):
    """每次调用都扫描目录，返回名字列表。"""
    base = os.path.join(ROOT, dataset)
    if not os.path.isdir(base):
        return []
    return sorted(name for name in os.listdir(base) if os.path.isdir(os.path.join(base, name)))
