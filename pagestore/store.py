"""页面快照存储，当前整页写盘。"""
import os

ROOT = "pages"


def _path(page_id, root=None):
    return os.path.join(root or ROOT, page_id + ".html")


def put(page_id, content, root=None):
    """整页直接写，重复内容各存一份。"""
    path = _path(page_id, root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def get(page_id, root=None):
    with open(_path(page_id, root), encoding="utf-8") as handle:
        return handle.read()


def delete(page_id, root=None):
    """删除整个页面，没有块引用。"""
    os.remove(_path(page_id, root))
    return True
