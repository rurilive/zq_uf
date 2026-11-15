import unittest

from jobwindow import close, open


class WindowTest(unittest.TestCase):
    def test_open_ignores_upstream(self):
        result = open({"name": "w1", "upstream": ["job-a"]})
        self.assertTrue(result["opened"])
        self.assertFalse(result["waited"])

    def test_open_has_no_catch_up(self):
        self.assertFalse(open({"name": "w2"})["catch_up"])

    def test_close_aborts_nothing(self):
        self.assertEqual(close({"name": "w1"})["aborted"], [])
