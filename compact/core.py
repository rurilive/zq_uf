"""把目录里的所有文件合并成一个。"""
import os


def compact(root, policy=None):
    merged = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                merged.extend(handle.read().splitlines())
    out = os.path.join(root, "merged.txt")
    with open(out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(merged))
    return out
