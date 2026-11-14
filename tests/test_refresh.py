import importlib
import unittest

from accountrefresh import get_token, refresh, stats

module = importlib.import_module("accountrefresh.refresh")


class RefreshTest(unittest.TestCase):
    def setUp(self):
        module._counts.clear()
        module._seq = 0

    def test_get_token_refreshes(self):
        first = get_token("a")["token"]
        second = get_token("a")["token"]
        self.assertNotEqual(first, second)

    def test_every_call_counts(self):
        get_token("a")
        get_token("a")
        self.assertEqual(stats()["a"], 2)

    def test_no_backoff(self):
        self.assertNotIn("next_try", refresh("a"))
