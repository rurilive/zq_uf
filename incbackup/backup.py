"""数据快照备份，当前每次全量复制。"""
import os
import shutil

TAG_FILE = "TAGS.json"


def backup(root, dest="backups"):
    """每次拷一份完整目录，用递增编号当标签。"""
    os.makedirs(dest, exist_ok=True)
    existing = sorted(name for name in os.listdir(dest) if name.startswith("snap-"))
    tag = "snap-%04d" % (len(existing) + 1)
    target = os.path.join(dest, tag)
    shutil.copytree(root, target)
    return {"tag": tag, "path": target, "files": sum(len(files) for _, _, files in os.walk(target))}


def restore(tag, dest="restore", source="backups"):
    """整份拷回，不做链校验。"""
    src = os.path.join(source, tag)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest
