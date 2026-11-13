"""按文件修改时间删除。"""
import os
import time


def plan(root, days=30):
    cutoff = time.time() - days * 86400
    return [
        os.path.join(root, name)
        for name in sorted(os.listdir(root))
        if os.path.isfile(os.path.join(root, name))
        and os.path.getmtime(os.path.join(root, name)) < cutoff
    ]


def apply(paths):
    removed = []
    for path in paths:
        os.remove(path)
        removed.append(path)
    return removed
