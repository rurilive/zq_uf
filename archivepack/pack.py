"""采集附件归档，当前按URL末段命名并直接覆盖。"""
import os


def pack(files, root):
    """逐条写盘，重名直接覆盖，没有清单。"""
    os.makedirs(root, exist_ok=True)
    saved = []
    for item in files:
        name = item["url"].rstrip("/").split("/")[-1] or "index"
        path = os.path.join(root, name)
        with open(path, "wb") as handle:
            handle.write(item.get("data", b""))
        saved.append(path)
    return saved


def restore(root):
    """读目录里的全部文件，顺序不保证。"""
    rows = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            rows.append({"name": name, "size": os.path.getsize(path)})
    return rows
