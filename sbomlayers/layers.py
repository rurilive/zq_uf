"""镜像组件清单，当前逐层拼接。"""

_IMAGES = {}


def reset():
    """清空镜像记录。"""
    _IMAGES.clear()
    return {"images": 0}


def sbom(image):
    """把所有层的组件直接堆在一起，删除记录不参与合并。"""
    if not isinstance(image, dict) or not image.get("image"):
        raise ValueError("image is required")
    layers = image.get("layers") or []
    components = []
    for layer in layers:
        for item in layer.get("add", []):
            components.append({
                "name": item.get("name", ""),
                "version": item.get("version", ""),
            })
    record = {
        "image": image["image"],
        "components": components,
        "layers": len(layers),
        "digest": "",
        "diff": [],
    }
    _IMAGES[image["image"]] = record
    return dict(record)


def report():
    """组件数按出现次数累加，重复项不去重。"""
    records = list(_IMAGES.values())
    return {
        "images": len(records),
        "components": sum(len(record["components"]) for record in records),
        "digest": "",
        "diff": [],
    }
