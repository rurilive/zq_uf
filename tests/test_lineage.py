import unittest

from rowlineage import record, reset, trace


class LineageTest(unittest.TestCase):
    def setUp(self):
        reset()
        record({"job": "j1", "inputs": ["raw.orders"], "outputs": ["dwd.orders"],
                "keys": {"raw.orders.id": "dwd.orders.order_id"}})

    def test_table_trace(self):
        self.assertEqual(len(trace("dwd.orders")), 1)

    def test_key_ignored(self):
        self.assertEqual(trace("dwd.orders", "order_id"), trace("dwd.orders", "nope"))

    def test_no_row_edges(self):
        self.assertEqual(set(trace("raw.orders")[0]), {"job", "inputs", "outputs"})
