"""附件归档，当前整文件直传。"""
import os


def upload(key, stream, root="archive"):
    """一次读完写盘，没有分片和清单。"""
    data = stream.read()
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, key)
    with open(path, "wb") as handle:
        handle.write(data)
    return {"key": key, "size": len(data), "path": path}


def reconcile(manifest):
    """不扫描对象，永远报一致。"""
    return {"missing": [], "orphans": [], "corrupt": []}
