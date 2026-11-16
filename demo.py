from firmware import report, unpack

IMAGE = {
    "name": "fw-a100",
    "format": "squashfs",
    "children": [
        {
            "name": "bin",
            "type": "dir",
            "children": [{"name": "busybox", "type": "file", "size": 900}],
        },
        {"name": "etc", "type": "dir", "children": [{"name": "passwd", "type": "file", "size": 40}]},
    ],
}

result = unpack(IMAGE)
print("layers", result["layers"])
print(result["files"])
print(report())

# TODO: 第二段演示（实现后补）
