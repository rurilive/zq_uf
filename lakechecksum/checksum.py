"""文件校验，当前整文件哈希。"""
import hashlib


def verify(path):
    """每次全量读取算 SHA256。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return {"sha256": digest.hexdigest(), "blocks": 1}


def repair(partition):
    """没有修复动作。"""
    return {"repaired": 0}
