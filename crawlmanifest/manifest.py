"""采集清单，当前单文件。"""
import json
import os


def write(manifest, root="manifests"):
    """一个任务一个文件，直接覆盖。"""
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "manifest.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False)
    return path


def verify(path):
    """只检查文件能否解析。"""
    try:
        with open(path, encoding="utf-8") as handle:
            json.load(handle)
        return {"ok": True, "missing": [], "corrupt": []}
    except Exception:
        return {"ok": False, "missing": [], "corrupt": [path]}
