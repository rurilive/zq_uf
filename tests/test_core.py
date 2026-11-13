import os
import tempfile
import time
import unittest
from unittest import mock

from compact import compact


class CompactTest(unittest.TestCase):
    def test_merges_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.txt"), "w").write("1")
            open(os.path.join(tmp, "b.txt"), "w").write("2")
            out = compact(tmp)
            self.assertEqual(open(out).read(), "1\n2")

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = compact(tmp)
            self.assertEqual(open(out).read(), "")

    def test_skips_files_over_size_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "small.txt"), "w").write("1")
            open(os.path.join(tmp, "large.txt"), "w").write("x" * 100)
            out = compact(tmp, {"max_file_size": 10})
            self.assertEqual(open(out).read(), "1")
            self.assertEqual(open(os.path.join(tmp, "large.txt")).read(), "x" * 100)

    def test_ignores_files_written_during_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.path.join(tmp, "old.txt")
            open(old, "w").write("1")
            past = time.time() - 7200
            os.utime(old, (past, past))
            open(os.path.join(tmp, "new.txt"), "w").write("2")
            out = compact(tmp, {"min_age_seconds": 3600})
            self.assertEqual(open(out).read(), "1")
            self.assertEqual(open(os.path.join(tmp, "new.txt")).read(), "2")

    def test_cleans_up_temp_file_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.txt"), "w").write("1")
            with mock.patch("compact.core.os.replace", side_effect=OSError("boom")):
                self.assertRaises(OSError, compact, tmp)
            self.assertEqual(sorted(os.listdir(tmp)), ["a.txt"])
            self.assertEqual(open(os.path.join(tmp, "a.txt")).read(), "1")

    def test_rolls_back_on_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.txt"), "w").write("1")
            with mock.patch("compact.core._stats", side_effect=[(1, "a"), (2, "b")]):
                self.assertRaises(RuntimeError, compact, tmp)
            self.assertEqual(sorted(os.listdir(tmp)), ["a.txt"])
            self.assertEqual(open(os.path.join(tmp, "a.txt")).read(), "1")
