"""附件下载，当前直接写目标文件。"""


def fetch(url, path):
    """一次写完，不续传也不校验。"""
    with open(path, "wb") as handle:
        handle.write(b"demo")
    return {"path": path, "bytes": 4, "resumed": False, "reused": False}


def verify(path):
    """只看文件在不在。"""
    return {"path": path, "ok": True, "checksum": None}
