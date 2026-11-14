import unittest

from rowversion import get, reset, update


class VersionTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_update_overwrites(self):
        update("k", {"v": 2}, 2)
        update("k", {"v": 1}, 1)
        self.assertEqual(get("k")["v"], 1)

    def test_no_conflict(self):
        self.assertNotIn("conflict", update("k", {"v": 1}, 1))

    def test_delete_no_version(self):
        update("k", {"v": 1}, 1)
        update("k", {"deleted": True}, 1)
        self.assertTrue(get("k")["deleted"])
