"""按目录名日期清理分区。"""
import os
import time


def plan(root, keep_days=30):
    cutoff = time.time() - keep_days * 86400
    return [
        os.path.join(root, name)
        for name in sorted(os.listdir(root))
        if os.path.isdir(os.path.join(root, name))
        and os.path.getmtime(os.path.join(root, name)) < cutoff
    ]


def apply(paths):
    removed = []
    for path in paths:
        os.rmdir(path)
        removed.append(path)
    return removed
