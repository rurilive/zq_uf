"""检查点，当前单文件覆盖。"""
import json
import os

ROOT = "checkpoints"


def _path(task, root=None):
    return os.path.join(root or ROOT, task + ".json")


def save(task, state, root=None):
    """直接覆盖，没有代际。"""
    path = _path(task, root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)
    return path


def load(task, generation=None, root=None):
    """generation被忽略，永远读同一个文件。"""
    with open(_path(task, root), encoding="utf-8") as handle:
        return json.load(handle)
