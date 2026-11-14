import unittest

from metricalert import emit, resolve
from metricalert import alert


class AlertTest(unittest.TestCase):
    def setUp(self):
        alert._sent.clear()

    def test_emit_counts(self):
        emit({"job": "j1"}); self.assertEqual(emit({"job": "j2"}), 2)

    def test_resolve_ok(self):
        self.assertTrue(resolve("j1")["resolved"])

    def test_no_aggregation(self):
        emit({"job": "j1"})
        self.assertEqual(len(alert._sent), 1)
