import copy
import unittest

from packerdetect import detect, diff, families, extract_features

UPX_SAMPLE = {
    "name": "drop.exe",
    "sections": ["UPX0", "UPX1", ".text"],
    "entry": "0x401000",
    "imports": ["LoadLibraryA"],
    "behavior": ["alloc_rwx"],
}

# 区段名已改，但 UPX 的结构指纹（可写空白解压区、入口在末区段、最小导入集）还在
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
}

UNPACKED_SAMPLE = {
    "name": "plain.exe",
    "sections": [
        {"name": ".text", "characteristics": "RX", "entropy": 6.1,
         "virtual_size": 0x2000, "virtual_address": "0x401000"},
        {"name": ".rdata", "characteristics": "R", "entropy": 4.2},
        {"name": ".data", "characteristics": "RW", "entropy": 3.5},
    ],
    "entry": "0x401050",
    "imports": ["CreateFileA", "WriteFile", "CloseHandle"],
    "behavior": [],
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


class DetectTest(unittest.TestCase):
    def test_known_section_hits_family_with_high_confidence(self):
        out = detect(UPX_SAMPLE)
        self.assertEqual(out["packer"], "upx")
        self.assertEqual(out["confidence"], "high")
        # 区段名强命中之外，结构和行为依据一并列出
        self.assertEqual(
            out["evidence"],
            ["section:upx0", "section:upx1", "structure:minimal_imports", "behavior:alloc_rwx"],
        )
        self.assertFalse(out["pending"])

    def test_renamed_sections_recognized_by_structure(self):
        out = detect(RENAMED_UPX)
        self.assertEqual(out["packer"], "upx")
        self.assertEqual(out["confidence"], "medium")
        self.assertFalse(out["pending"])
        # 依据里没有任何 section: 名字命中，全是结构/行为
        self.assertFalse(any(item.startswith("section:") for item in out["evidence"]))
        self.assertIn("structure:writable_zero_raw", out["evidence"])
        self.assertIn("structure:entry_in_last_section", out["evidence"])
        self.assertIn("structure:minimal_imports", out["evidence"])
        self.assertIn("behavior:alloc_rwx", out["evidence"])

    def test_unknown_packer_marked_pending_with_structural_profile(self):
        out = detect(UNKNOWN_PACKED)
        self.assertEqual(out["packer"], "unknown")
        self.assertTrue(out["pending"])
        self.assertEqual(out["confidence"], "low")
        self.assertTrue(out["evidence"])  # 每条通用信号都列出来
        profile = out["structural"]
        self.assertEqual(profile["rwx_sections"], [".blob"])
        self.assertEqual(profile["high_entropy_sections"], [".blob"])
        self.assertTrue(profile["minimal_imports"])

    def test_clean_sample_is_unpacked(self):
        out = detect(UNPACKED_SAMPLE)
        self.assertEqual(out["packer"], "unpacked")
        self.assertEqual(out["evidence"], [])
        self.assertFalse(out["pending"])
        self.assertIsNone(out["unpacked"])

    def test_single_weak_signal_is_pending_not_unpacked(self):
        sample = {
            "name": "weak.exe",
            "sections": [{"name": ".text", "characteristics": "RX"},
                         {"name": ".data", "characteristics": "RWX"}],
            "entry": "0x401000",
            "imports": ["CreateFileA", "WriteFile"],
        }
        out = detect(sample)
        self.assertEqual(out["packer"], "unknown")
        self.assertTrue(out["pending"])

    def test_reject_sample_without_sections(self):
        with self.assertRaises(ValueError):
            detect({"name": "empty.exe"})

    def test_families_lists_known_sections(self):
        self.assertEqual(families()["upx"], ["upx0", "upx1"])

    def test_extract_features_is_name_independent(self):
        features = extract_features(RENAMED_UPX)
        self.assertEqual(features["writable_zero_raw_sections"], [".x1"])
        self.assertEqual(features["rwx_sections"], [".x2"])
        self.assertTrue(features["entry_in_last_section"])
        self.assertTrue(features["minimal_imports"])

    def test_detect_and_diff_do_not_mutate_input(self):
        sample = copy.deepcopy(RENAMED_UPX)
        detect(sample)
        diff(sample, UNPACKED_SAMPLE)
        self.assertEqual(sample, RENAMED_UPX)

    def test_integer_entry_and_mixed_section_forms(self):
        sample = {
            "name": "edge.exe",
            "sections": [
                {"name": ".X1", "characteristics": "RW", "raw_size": 0,
                 "virtual_size": 4096, "virtual_address": 4198400},
                {"name": ".x2", "characteristics": "rwx", "entropy": 7.8,
                 "raw_size": 2048, "virtual_size": 4096, "virtual_address": 4202496},
            ],
            "entry": 4202512,
            "imports": ["LoadLibraryA", "GetProcAddress"],
            "behavior": ["alloc_rwx"],
        }
        out = detect(sample)
        self.assertEqual(out["packer"], "upx")
        self.assertEqual(out["confidence"], "medium")


class DiffTest(unittest.TestCase):
    def test_diff_lists_sections_entry_imports_and_recheck(self):
        after = {
            "name": "drop.exe",
            "sections": [".text", ".rdata"],
            "entry": "0x401050",
            "imports": ["LoadLibraryA", "CreateFileA"],
            "behavior": [],
        }
        out = diff(UPX_SAMPLE, after)
        self.assertEqual(out["section_added"], [".rdata"])
        self.assertEqual(out["section_removed"], ["upx0", "upx1"])
        self.assertTrue(out["entry_changed"])
        self.assertEqual(out["entry_before"], "0x401000")
        self.assertEqual(out["entry_after"], "0x401050")
        self.assertEqual(out["imports_added"], ["createfilea"])
        self.assertEqual(out["import_delta"], ["import_added:createfilea"])
        self.assertIn("alloc_rwx", out["behavior_removed"])
        self.assertEqual(out["recheck"]["packer"], "unpacked")
        self.assertTrue(out["clean"])
        self.assertTrue(out["changed"])

    def test_diff_same_sample_is_unchanged_and_clean_flag_reflects_packer(self):
        out = diff(UPX_SAMPLE, dict(UPX_SAMPLE))
        self.assertFalse(out["changed"])
        # 原样仍是 upx，复检不通过 -> 不算脱干净
        self.assertFalse(out["clean"])
        self.assertEqual(out["recheck"]["packer"], "upx")

    def test_diff_detects_unclean_unpack(self):
        # 入口回了 .text，但壳区段和 RWX 还留着
        after = dict(UPX_SAMPLE, entry="0x401050")
        out = diff(UPX_SAMPLE, after)
        self.assertTrue(out["entry_changed"])
        self.assertFalse(out["clean"])


if __name__ == "__main__":
    unittest.main()
