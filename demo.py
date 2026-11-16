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
print("rootfs", result["rootfs"], "key_dirs", result["key_dirs"])
print([f["path"] for f in result["files"]])

# 第二段演示：嵌套固件，一层解不开，失败保留现场
NESTED = {
    "name": "fw-b200",
    "format": "trx",
    "offset": 0,
    "children": [
        {"name": "kernel", "type": "file", "size": 1500000, "offset": 0x1C},
        {
            "name": "rootfs.squashfs",
            "type": "image",
            "format": "squashfs",
            "offset": 0x100000,
            "children": [
                {"name": "bin", "type": "dir",
                 "children": [{"name": "busybox", "type": "file", "size": 900}]},
                {"name": "etc", "type": "dir",
                 "children": [{"name": "passwd", "type": "file", "size": 40}]},
            ],
        },
        {"name": "data.jffs2", "type": "image", "format": "jffs2",
         "offset": 0x500000, "error": "bad magic"},
    ],
}

nested = unpack(NESTED)
print("layers", nested["layers"], "rootfs", nested["rootfs"])
for entry in nested["layer_map"]:
    print("  layer", entry["layer"], entry["format"] or "-",
          entry["path"], "offset", entry["offset"], entry["status"])
print("failed at", nested["failed"])

rep = report()
print("images", rep["images"], "files", rep["files"], "size", rep["total_size"])
for name, summary in rep["summaries"].items():
    print(name, "->", summary["rootfs"], summary["key_configs"])
