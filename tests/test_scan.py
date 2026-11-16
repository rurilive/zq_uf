import unittest

from packerdetect import detect, diff, families

UPX_SAMPLE = {
    "name": "drop.exe",
    "sections": ["UPX0", "UPX1", ".text"],
    "entry": "0x401000",
    "imports": ["LoadLibraryA"],
    "behavior": ["alloc_rwx"],
}

RENAMED = {
    "name": "drop2.exe",
    "sections": [".x1", ".x2", ".text"],
    "entry": "0x401000",
    "imports": ["LoadLibraryA"],
    "behavior": ["alloc_rwx"],
}


class DetectTest(unittest.TestCase):
    def test_known_section_hits_family(self):
        out = detect(UPX_SAMPLE)
        self.assertEqual(out["packer"], "upx")
        self.assertEqual(out["evidence"], ["section:upx0", "section:upx1"])

    def test_renamed_sections_treated_as_unpacked(self):
        out = detect(RENAMED)
        self.assertEqual(out["packer"], "unpacked")
        self.assertEqual(out["evidence"], [])
        self.assertIsNone(out["unpacked"])

    def test_diff_only_compares_section_names(self):
        after = dict(UPX_SAMPLE, sections=[".text", ".rdata"], imports=["LoadLibraryA", "CreateFileA"])
        out = diff(UPX_SAMPLE, after)
        self.assertEqual(out["section_added"], [".rdata"])
        self.assertEqual(out["section_removed"], ["upx0", "upx1"])
        self.assertEqual(out["import_delta"], [])
        self.assertTrue(out["changed"])

    def test_diff_same_sections_is_unchanged(self):
        out = diff(UPX_SAMPLE, dict(UPX_SAMPLE))
        self.assertFalse(out["changed"])

    def test_reject_sample_without_sections(self):
        with self.assertRaises(ValueError):
            detect({"name": "empty.exe"})

    def test_families_lists_known_sections(self):
        self.assertEqual(families()["upx"], ["upx0", "upx1"])


if __name__ == "__main__":
    unittest.main()
