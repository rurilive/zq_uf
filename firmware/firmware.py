"""固件解包，当前把嵌套目录一次性摊平。"""

_IMAGES = {}


def _collect(node, prefix, files):
    name = str(node.get("name", "")).strip("/")
    path = prefix + "/" + name if prefix else "/" + name
    if node.get("type") == "dir":
        for child in node.get("children", []):
            _collect(child, path, files)
        return
    files.append(path)


def unpack(image):
    """只走一遍目录树，层数和根文件系统都不判断。"""
    if not isinstance(image, dict):
        raise ValueError("image must be a dict")
    children = image.get("children")
    if not isinstance(children, list):
        raise ValueError("children must be a list")
    name = image.get("name", "")
    files = []
    for child in children:
        _collect(child, "", files)
    files.sort()
    _IMAGES[name] = {"files": files, "format": image.get("format", "")}
    return {
        "image": name,
        "format": image.get("format", ""),
        "layers": 1,
        "files": files,
        "rootfs": None,
        "key_dirs": [],
        "failed": None,
    }


def inspect(image_name, path):
    """在已解包的镜像里找一条路径，找不到返回空。"""
    record = _IMAGES.get(image_name)
    if record is None:
        return {}
    for item in record["files"]:
        if item == path:
            return {"image": image_name, "path": item, "layer": 1}
    return {}


def report():
    """汇总文件数，根文件系统和失败点都是空的。"""
    total = 0
    for record in _IMAGES.values():
        total += len(record["files"])
    return {
        "images": len(_IMAGES),
        "files": total,
        "rootfs": {},
        "failed": [],
    }
