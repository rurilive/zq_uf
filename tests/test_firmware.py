import unittest

from firmware import inspect, report, unpack
from firmware import firmware

# 扁平镜像：本身就是根文件系统
FLAT = {
    "name": "fw-a100",
    "format": "squashfs",
    "children": [
        {
            "name": "bin",
            "type": "dir",
            "children": [{"name": "busybox", "type": "file", "size": 900}],
        },
        {"name": "etc", "type": "dir",
         "children": [{"name": "passwd", "type": "file", "size": 40}]},
    ],
}

# 嵌套镜像：trx 容器里套 squashfs 根文件系统，另有一层解不开
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
                {"name": "sbin", "type": "dir", "children": []},
                {"name": "etc", "type": "dir", "children": [
                    {"name": "passwd", "type": "file", "size": 40},
                    {"name": "inittab", "type": "file", "size": 120},
                ]},
            ],
        },
        {
            "name": "data.jffs2",
            "type": "image",
            "format": "jffs2",
            "offset": 0x500000,
            "error": "bad magic",
            "children": [
                {"name": "config", "type": "dir",
                 "children": [{"name": "nvram.cfg", "type": "file", "size": 64}]},
            ],
        },
    ],
}


def _nested_variant(name):
    """同结构、不同文件大小的另一型号固件。"""
    import copy
    img = copy.deepcopy(NESTED)
    img["name"] = name
    img["children"][0]["size"] = 1600000
    return img


class UnpackTest(unittest.TestCase):
    def setUp(self):
        firmware._IMAGES.clear()
        firmware._SIGNATURES.clear()

    def test_flat_image_is_rootfs(self):
        result = unpack(FLAT)
        self.assertEqual(result["layers"], 1)
        self.assertEqual(result["rootfs"], "/")
        self.assertEqual(result["key_dirs"], ["/bin", "/etc"])
        self.assertEqual(result["key_configs"], {"etc/passwd": "/etc/passwd"})
        self.assertIsNone(result["failed"])

    def test_files_carry_layer_size_offset(self):
        files = {f["path"]: f for f in unpack(FLAT)["files"]}
        self.assertEqual(files["/bin/busybox"]["layer"], 1)
        self.assertEqual(files["/bin/busybox"]["size"], 900)

    def test_nested_layers_and_offsets(self):
        result = unpack(NESTED)
        self.assertEqual(result["layers"], 2)
        layers = {(e["path"]): (e["layer"], e["offset"], e["status"])
                  for e in result["layer_map"]}
        self.assertEqual(layers["/"], (1, 0, "ok"))
        self.assertEqual(layers["/rootfs.squashfs"], (2, 0x100000, "ok"))
        self.assertEqual(layers["/data.jffs2"], (2, 0x500000, "failed"))

    def test_nested_rootfs_and_key_dirs(self):
        result = unpack(NESTED)
        self.assertEqual(result["rootfs"], "/rootfs.squashfs")
        self.assertEqual(result["key_dirs"],
                         ["/rootfs.squashfs/bin",
                          "/rootfs.squashfs/etc",
                          "/rootfs.squashfs/sbin"])
        self.assertEqual(result["key_configs"]["etc/inittab"],
                         "/rootfs.squashfs/etc/inittab")

    def test_failure_keeps_partial_results(self):
        result = unpack(NESTED)
        self.assertTrue(result["partial"])
        failed = result["failed"]
        self.assertEqual(failed["layer"], 2)
        self.assertEqual(failed["path"], "/data.jffs2")
        self.assertEqual(failed["offset"], 0x500000)
        self.assertIn("bad magic", failed["error"])
        # 失败层里已解出的部分不丢弃
        paths = [f["path"] for f in result["files"]]
        self.assertIn("/data.jffs2/config/nvram.cfg", paths)

    def test_unexpanded_container_is_failure(self):
        img = {"name": "fw-x", "format": "ubi", "children": [
            {"name": "volume.squashfs", "type": "image",
             "format": "squashfs", "offset": 0x2000},
        ]}
        result = unpack(img)
        self.assertEqual(result["failed"]["offset"], 0x2000)
        self.assertEqual(result["failed"]["layer"], 2)

    def test_same_structure_reuses_signature(self):
        first = unpack(NESTED)
        second = unpack(_nested_variant("fw-b201"))
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["signature"], second["signature"])

    def test_inspect_reports_layer(self):
        unpack(NESTED)
        hit = inspect("fw-b200", "/rootfs.squashfs/etc/passwd")
        self.assertEqual(hit["layer"], 2)
        self.assertEqual(hit["size"], 40)
        self.assertEqual(inspect("fw-b200", "/nope"), {})

    def test_invalid_input_raises(self):
        with self.assertRaises(ValueError):
            unpack("not-a-dict")
        with self.assertRaises(ValueError):
            unpack({"name": "x"})


class ReportTest(unittest.TestCase):
    def setUp(self):
        firmware._IMAGES.clear()
        firmware._SIGNATURES.clear()

    def test_report_summarizes_images(self):
        unpack(FLAT)
        unpack(NESTED)
        rep = report()
        self.assertEqual(rep["images"], 2)
        self.assertEqual(rep["files"], 2 + 5)
        self.assertEqual(rep["total_size"], 940 + 1500000 + 900 + 40 + 120 + 64)
        self.assertEqual(rep["rootfs"]["fw-b200"], "/rootfs.squashfs")
        self.assertEqual(len(rep["failed"]), 1)
        self.assertEqual(rep["failed"][0]["image"], "fw-b200")
        summary = rep["summaries"]["fw-b200"]
        self.assertEqual(summary["layers"], 2)
        self.assertTrue(summary["partial"])

    def test_same_model_unpacks_are_comparable(self):
        unpack(NESTED)
        unpack(_nested_variant("fw-b201"))
        rep = report()
        self.assertEqual(len(rep["families"]), 1)
        family = next(iter(rep["families"].values()))
        self.assertEqual(family, ["fw-b200", "fw-b201"])

    def test_repeated_unpack_is_stable(self):
        unpack(NESTED)
        first = report()["summaries"]["fw-b200"]
        unpack(NESTED)
        self.assertEqual(first, report()["summaries"]["fw-b200"])


if __name__ == "__main__":
    unittest.main()
