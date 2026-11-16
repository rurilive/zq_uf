import os
import tempfile
import unittest

from packerdetect import (
    UnpackParamStore,
    detect,
    get_unpack_params,
    set_unpack_params,
    unpack,
)

RENAMED_UPX = {
    "name": "renamed.exe",
    "sections": [
        {"name": ".x1", "characteristics": "RW", "raw_size": 0,
         "virtual_size": 0x1000, "virtual_address": "0x401000"},
        {"name": ".x2", "characteristics": "RWX", "entropy": 7.8, "raw_size": 0x800,
         "virtual_size": 0x1000, "virtual_address": "0x402000"},
    ],
    "entry": "0x402010",
    "imports": ["LoadLibraryA", "GetProcAddress"],
    "behavior": ["alloc_rwx"],
    "payload": {
        "sections": [".text", ".rdata", ".data"],
        "entry": "0x401000",
        "imports": ["CreateFileA", "WriteFile"],
        "behavior": [],
    },
}

THEMIDA_SAMPLE = {
    "name": "hard.exe",
    "sections": [{"name": ".themida", "characteristics": "RWX", "entropy": 7.9}],
    "entry": "0x405000",
    "imports": [],
    "behavior": ["anti_debug", "anti_vm"],
}

UNKNOWN_PACKED = {
    "name": "mystery.exe",
    "sections": [
        {"name": ".text", "characteristics": "RX", "entropy": 6.1},
        {"name": ".blob", "characteristics": "RWX", "entropy": 7.9},
    ],
    "entry": "0x403020",
    "imports": ["LoadLibraryA"],
    "behavior": ["self_modify", "dynamic_api_resolve"],
}

UNPACKED_SAMPLE = {
    "name": "plain.exe",
    "sections": [".text", ".rdata", ".data"],
    "entry": "0x401050",
    "imports": ["CreateFileA"],
    "behavior": [],
}


class UnpackTest(unittest.TestCase):
    def test_unpack_success_rebuilds_and_diffs(self):
        res = unpack(RENAMED_UPX)
        self.assertTrue(res["ok"])
        self.assertEqual(res["packer"], "upx")
        self.assertIsNone(res["reason"])
        # 默认家族参数被自动复用
        self.assertEqual(res["params"]["tool"], "upx -d")

        comparison = res["diff"]
        self.assertEqual(comparison["section_removed"], [".x1", ".x2"])
        self.assertEqual(comparison["section_added"], [".text", ".rdata", ".data"])
        self.assertTrue(comparison["entry_changed"])
        self.assertEqual(comparison["imports_added"], ["createfilea", "writefile"])
        self.assertEqual(
            comparison["imports_removed"],
            ["loadlibrarya", "getprocaddress"],
        )
        self.assertEqual(
            comparison["import_delta"],
            [
                "import_added:createfilea",
                "import_added:writefile",
                "import_removed:loadlibrarya",
                "import_removed:getprocaddress",
            ],
        )
        # 脱后重新识别：已经没有壳特征
        self.assertEqual(comparison["recheck"]["packer"], "unpacked")
        self.assertTrue(res["clean"])

    def test_failure_keeps_original_and_reason(self):
        # 显式覆盖家族参数，保证用例不受全局参数库其他用例影响
        res = unpack(THEMIDA_SAMPLE, {"bypass": []})
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "anti_debug")
        self.assertIn("反调试", res["reason_detail"])
        # 原样本完整保留，没有伪造产物
        self.assertEqual(res["original"]["name"], "hard.exe")
        self.assertNotIn("unpacked", res)

    def test_per_call_bypass_params_unblock(self):
        res = unpack(THEMIDA_SAMPLE, {"bypass": ["anti_debug", "anti_vm"]})
        self.assertTrue(res["ok"], res.get("reason_detail"))
        self.assertTrue(res["clean"])
        # 一次性参数不写回家族库
        self.assertEqual(get_unpack_params("themida")["bypass"], ["anti_debug", "anti_vm"])

    def test_other_blocker_reported_with_code(self):
        sample = dict(THEMIDA_SAMPLE, behavior=["encrypted_iat"])
        res = unpack(sample)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "encrypted_iat")
        self.assertEqual(res["original"]["behavior"], ["encrypted_iat"])

    def test_unknown_family_requires_explicit_params(self):
        res = unpack(UNKNOWN_PACKED)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "no_unpack_params")
        self.assertIn("未知壳", res["reason_detail"])

        forced = unpack(UNKNOWN_PACKED, {"tool": "manual", "method": "dynamic_unpack"})
        self.assertTrue(forced)
        # 未知家族的检测依据一并带出，供人工判断
        self.assertEqual(forced["packer"], "unknown")
        self.assertTrue(forced["evidence"])

    def test_unpacked_sample_is_not_processed(self):
        res = unpack(UNPACKED_SAMPLE)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "not_packed")
        self.assertEqual(res["original"]["name"], "plain.exe")

    def test_family_params_reused_across_samples(self):
        # 同一家族两个样本，参数只设一次，第二个样本自动复用
        store = UnpackParamStore()
        store.set("upx", {"tool": "custom-upx", "bypass": ["magic_check"]})
        self.assertEqual(store.get("upx")["tool"], "custom-upx")

        other = dict(RENAMED_UPX, name="renamed2.exe")
        # 模块级入口同样按家族取默认参数
        res = unpack(other)
        self.assertTrue(res["ok"])
        self.assertEqual(res["packer"], "upx")

    def test_param_store_persists_to_json(self):
        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        try:
            store = UnpackParamStore(path)
            store.set("upx", {"tool": "upx-pro", "bypass": ["magic_check"]})
            self.assertTrue(os.path.exists(path))

            reloaded = UnpackParamStore(path)
            params = reloaded.get("upx")
            self.assertEqual(params["tool"], "upx-pro")
            self.assertIn("magic_check", params["bypass"])
            # 默认参数仍然保留，持久化是合并而不是覆盖
            self.assertEqual(params["method"], "static_unpack")
            self.assertIn("themida", reloaded.families())
        finally:
            os.remove(path)

    def test_module_level_set_and_get(self):
        # 用独立家族做写入/读取，不改动内置家族参数，避免用例间互相影响
        self.assertIsNone(get_unpack_params("custom_x"))
        updated = set_unpack_params("custom_x", {"tool": "manual", "bypass": ["custom_check"]})
        self.assertEqual(updated["tool"], "manual")
        self.assertEqual(get_unpack_params("custom_x")["bypass"], ["custom_check"])


if __name__ == "__main__":
    unittest.main()
