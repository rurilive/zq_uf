import unittest

from samplededupe import ingest, report, reset


class StoreTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_new_sample_is_added(self):
        out = ingest({"name": "locker-a.bin", "digest": "aa11", "size": 2048})
        self.assertTrue(out["added"])
        self.assertEqual(out["status"], "added")

    def test_same_digest_is_duplicate(self):
        ingest({"name": "locker-a.bin", "digest": "aa11", "family": "ransom"})
        out = ingest({"name": "locker-b.bin", "digest": "aa11", "family": "ransom"})
        self.assertEqual(out["status"], "duplicate")
        self.assertFalse(out["added"])
        self.assertEqual(out["family"], "ransom")

    def test_one_byte_change_counts_as_new(self):
        ingest({"name": "locker-a.bin", "digest": "aa11"})
        out = ingest({"name": "locker-a1.bin", "digest": "aa12"})
        self.assertTrue(out["added"])

    def test_missing_family_is_unlabeled(self):
        out = ingest({"name": "drop.bin", "digest": "cc33"})
        self.assertEqual(out["family"], "unlabeled")

    def test_report_counts_total_and_duplicates(self):
        ingest({"name": "locker-a.bin", "digest": "aa11", "family": "ransom"})
        ingest({"name": "locker-b.bin", "digest": "aa11", "family": "ransom"})
        ingest({"name": "rat.bin", "digest": "cc33", "family": "rat"})
        out = report()
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["duplicates"], 1)
        self.assertEqual(out["families"], {"ransom": 1, "rat": 1})


if __name__ == "__main__":
    unittest.main()
