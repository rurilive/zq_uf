"""文件去重，当前按路径各存一份。"""
import os
import shutil


def add(path, root="store"):
    """按文件名复制，同内容不同名也各存一份。"""
    os.makedirs(root, exist_ok=True)
    dest = os.path.join(root, os.path.basename(path))
    shutil.copy2(path, dest)
    return dest


def remove(path, root="store"):
    """直接删对应副本，不检查共享。"""
    dest = os.path.join(root, os.path.basename(path))
    if os.path.exists(dest):
        os.remove(dest)
    return True
