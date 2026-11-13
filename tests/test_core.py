import os
import tempfile
import unittest

from snapver import Snapshot


class SnapverTest(unittest.TestCase):
    def test_create_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "data")
            store = os.path.join(tmp, "snaps")
            os.makedirs(root)
            open(os.path.join(root, "a.txt"), "w").write("v1")
            snap = Snapshot(root, store)
            snap.create("s1")
            open(os.path.join(root, "a.txt"), "w").write("v2")
            snap.restore("s1")
            self.assertEqual(open(os.path.join(root, "a.txt")).read(), "v1")
