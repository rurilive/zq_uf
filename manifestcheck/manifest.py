"""数据文件清单，当前直接覆盖写。"""


def write(manifest):
    """直接把清单写到目标路径，不做原子写。"""
    path = manifest.get("path", "manifest.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(manifest.get("files", [])))
    return {"path": path, "files": len(manifest.get("files", [])), "checksum": None}


def verify(path):
    """只看文件是否存在。"""
    import os
    return {"path": path, "ok": os.path.exists(path), "missing": [], "extra": [],
            "corrupt": [], "checksum": None}
