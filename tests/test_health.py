import unittest

from proxycheck import check, pick


class ProxycheckTest(unittest.TestCase):
    def test_check_records_probe(self):
        results = check(["p1", "p2"], lambda proxy: (proxy == "p1", 10))
        self.assertEqual(results[0], {"proxy": "p1", "ok": True, "latency": 10})
        self.assertFalse(results[1]["ok"])

    def test_pick_first_healthy(self):
        results = check(["p1", "p2", "p3"], lambda proxy: (proxy != "p1", 5))
        self.assertEqual(pick(results), "p2")

    def test_pick_none_when_all_bad(self):
        results = check(["p1"], lambda proxy: (False, 99))
        self.assertIsNone(pick(results))
