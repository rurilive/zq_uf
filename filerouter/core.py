"""按扩展名分发文件。"""
import os
import shutil


def route(path, target_root):
    ext = os.path.splitext(path)[1].lstrip(".") or "unknown"
    target = os.path.join(target_root, ext)
    os.makedirs(target, exist_ok=True)
    shutil.move(path, os.path.join(target, os.path.basename(path)))
    return os.path.join(target, os.path.basename(path))
