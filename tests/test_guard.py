import unittest

from crawlguard import admit, reload


class GuardTest(unittest.TestCase):
    def test_everything_admitted(self):
        self.assertTrue(admit({"id": "t1", "site": "blocked"})["admitted"])

    def test_no_audit(self):
        self.assertEqual(admit({"id": "t2"})["audit"], [])

    def test_reload_is_noop(self):
        self.assertFalse(reload()["reloaded"])
