"""固件解包：识别容器格式逐层展开，定位根文件系统，失败保留现场。

镜像用嵌套 dict 描述：
- 容器层：带 "format"（或 type 为 image/container），可有 "offset"（原始偏移）
  和 "children"；解不开时带 "error"，已解出的 children 仍保留。
- 目录：type 为 "dir"，带 "children"。
- 文件：带 "size" / "offset"，无 children。
"""

import hashlib

_IMAGES = {}
_SIGNATURES = {}

_CONTAINER_TYPES = ("image", "container")
# 根文件系统的内容特征：出现越多越可能是 rootfs
_ROOTFS_MARKERS = ("bin", "sbin", "etc", "lib", "usr", "init", "linuxrc")
# rootfs 内需要标出的关键目录（相对 rootfs 根）
_KEY_DIRS = ("bin", "sbin", "etc", "lib", "usr", "var", "www",
             "etc/init.d", "etc/config", "etc/rc.d")
# 关键配置文件（相对 rootfs 根）
_KEY_CONFIGS = ("etc/passwd", "etc/shadow", "etc/group", "etc/inittab",
                "etc/fstab", "etc/rcS", "etc/init.d/rcS",
                "etc/config/network", "etc/config/system")


def _node_name(node):
    return str(node.get("name", "")).strip("/")


def _is_container(node):
    return node.get("type") in _CONTAINER_TYPES or bool(node.get("format"))


def _walk(node, prefix, layer, ctx):
    """递归展开一层；容器节点的子节点层数 +1。失败只记录，不中断。"""
    name = _node_name(node)
    path = (prefix + "/" + name) if name else (prefix or "/")
    ctx["layers"] = max(ctx["layers"], layer)
    container = _is_container(node)
    error = node.get("error")

    if container:
        ctx["layer_map"].append({
            "layer": layer,
            "path": path,
            "format": node.get("format", ""),
            "offset": node.get("offset"),
            "status": "failed" if error else "ok",
        })
        ctx["containers"].append((path, node))
    if error:
        ctx["failures"].append({
            "layer": layer,
            "path": path,
            "offset": node.get("offset"),
            "error": str(error),
        })

    children = node.get("children")
    if children is None:
        if container:
            if not error:
                ctx["failures"].append({
                    "layer": layer,
                    "path": path,
                    "offset": node.get("offset"),
                    "error": "container not expanded",
                })
                ctx["layer_map"][-1]["status"] = "failed"
        else:
            ctx["files"].append({
                "path": path,
                "layer": layer,
                "size": int(node.get("size", 0) or 0),
                "offset": node.get("offset"),
            })
        return
    if not isinstance(children, list):
        ctx["failures"].append({
            "layer": layer,
            "path": path,
            "offset": node.get("offset"),
            "error": "children is not a list",
        })
        return

    if path != "/":
        ctx["dirs"].append(path)
    for child in children:
        if not isinstance(child, dict):
            continue
        child_layer = layer + 1 if _is_container(child) else layer
        _walk(child, path if path != "/" else "", child_layer, ctx)


def _find_rootfs(containers):
    """按内容特征给每个容器层打分，得分最高（>=2）的就是根文件系统。"""
    best_path, best_score = None, 0
    for path, node in containers:
        names = {_node_name(c) for c in node.get("children", []) if isinstance(c, dict)}
        score = len(names & set(_ROOTFS_MARKERS))
        if score > best_score:
            best_path, best_score = path, score
    return best_path if best_score >= 2 else None


def _relative(path, rootfs):
    if rootfs == "/":
        return path.lstrip("/")
    if path == rootfs or not path.startswith(rootfs + "/"):
        return None
    return path[len(rootfs) + 1:]


def _signature(layer_map, dirs):
    """结构签名：只看层级/格式/目录骨架，同型号不同构建签名一致。"""
    skeleton = [(e["layer"], e["format"], e["status"]) for e in layer_map]
    skeleton += [("dir", d) for d in sorted(dirs)]
    return hashlib.sha1(repr(skeleton).encode("utf-8")).hexdigest()[:12]


def unpack(image):
    """逐层展开镜像，返回层级结构、根文件系统位置、关键目录和失败点。

    失败时不抛错（输入非法除外），已解出的部分保留在结果里。
    """
    if not isinstance(image, dict):
        raise ValueError("image must be a dict")
    if not isinstance(image.get("children"), list):
        raise ValueError("children must be a list")
    name = image.get("name", "")

    ctx = {"files": [], "dirs": [], "failures": [], "layer_map": [],
           "containers": [], "layers": 1}
    _walk(dict(image, name=""), "", 1, ctx)

    rootfs = _find_rootfs(ctx["containers"])
    key_dirs, key_configs = [], {}
    if rootfs is not None:
        for d in ctx["dirs"]:
            rel = _relative(d, rootfs)
            if rel in _KEY_DIRS:
                key_dirs.append(d)
        for f in ctx["files"]:
            rel = _relative(f["path"], rootfs)
            if rel in _KEY_CONFIGS:
                key_configs[rel] = f["path"]
    key_dirs.sort()

    files = sorted(ctx["files"], key=lambda f: f["path"])
    sig = _signature(ctx["layer_map"], ctx["dirs"])
    reused = sig in _SIGNATURES
    _SIGNATURES.setdefault(sig, {"images": [], "layers": ctx["layers"],
                                 "rootfs": rootfs})
    if name not in _SIGNATURES[sig]["images"]:
        _SIGNATURES[sig]["images"].append(name)

    result = {
        "image": name,
        "format": image.get("format", ""),
        "layers": ctx["layers"],
        "layer_map": ctx["layer_map"],
        "rootfs": rootfs,
        "key_dirs": key_dirs,
        "key_configs": key_configs,
        "files": files,
        "file_count": len(files),
        "total_size": sum(f["size"] for f in files),
        "failed": ctx["failures"][0] if ctx["failures"] else None,
        "failures": ctx["failures"],
        "partial": bool(ctx["failures"]),
        "signature": sig,
        "reused": reused,
    }
    _IMAGES[name] = result
    return result


def inspect(image_name, path):
    """在已解包的镜像里找一条路径，返回所在层、大小和偏移，找不到返回空。"""
    record = _IMAGES.get(image_name)
    if record is None:
        return {}
    for item in record["files"]:
        if item["path"] == path:
            return {"image": image_name, **item}
    return {}


def report():
    """汇总所有已解包镜像：大小、文件数、关键配置位置，同结构分组可对比。"""
    summaries, rootfs, failed = {}, {}, []
    for name, rec in sorted(_IMAGES.items()):
        summaries[name] = {
            "format": rec["format"],
            "layers": rec["layers"],
            "files": rec["file_count"],
            "size": rec["total_size"],
            "rootfs": rec["rootfs"],
            "key_dirs": rec["key_dirs"],
            "key_configs": rec["key_configs"],
            "signature": rec["signature"],
            "partial": rec["partial"],
        }
        rootfs[name] = rec["rootfs"]
        for f in rec["failures"]:
            failed.append({"image": name, **f})
    families = {sig: sorted(info["images"])
                for sig, info in sorted(_SIGNATURES.items())}
    return {
        "images": len(_IMAGES),
        "files": sum(r["file_count"] for r in _IMAGES.values()),
        "total_size": sum(r["total_size"] for r in _IMAGES.values()),
        "rootfs": rootfs,
        "failed": failed,
        "summaries": summaries,
        "families": families,
    }
