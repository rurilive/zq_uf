import os
import tempfile
import time
import unittest

from partitiongc import apply, plan


class PartitiongcTest(unittest.TestCase):
    def test_plan_old_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.path.join(tmp, "2020-01-01")
            os.makedirs(old)
            stamp = time.time() - 90 * 86400
            os.utime(old, (stamp, stamp))
            self.assertEqual(plan(tmp, keep_days=30), [old])

    def test_recent_dir_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "today"))
            self.assertEqual(plan(tmp, keep_days=30), [])

    def test_apply_removes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old")
            os.makedirs(path)
            self.assertEqual(apply([path]), [path])
            self.assertFalse(os.path.exists(path))
