import os
import tempfile
import time
import unittest

from retention import apply, plan


class RetentionTest(unittest.TestCase):
    def test_plan_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.txt")
            open(path, "w").write("x")
            stamp = time.time() - 90 * 86400
            os.utime(path, (stamp, stamp))
            self.assertEqual(plan(tmp, 30), [path])

    def test_recent_file_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "new.txt"), "w").write("x")
            self.assertEqual(plan(tmp, 30), [])

    def test_apply_removes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.txt")
            open(path, "w").write("x")
            self.assertEqual(apply([path]), [path])
            self.assertFalse(os.path.exists(path))
