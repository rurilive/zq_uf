import unittest

from fieldlineage import record, reset, trace


class LineageTest(unittest.TestCase):
    def setUp(self):
        reset()
        record({"job": "j1", "inputs": ["raw.orders"], "outputs": ["dwd.orders"],
                "columns": [{"from": "raw.orders.amount", "to": "dwd.orders.total"}]})

    def test_table_level_trace(self):
        self.assertEqual(len(trace("dwd.orders")), 1)

    def test_field_argument_ignored(self):
        self.assertEqual(trace("dwd.orders", "total"), trace("dwd.orders", "no_such"))

    def test_no_edges(self):
        self.assertEqual(set(trace("raw.orders")[0].keys()), {"job", "inputs", "outputs"})
