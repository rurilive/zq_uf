import unittest

from datasearch import search


class SearchTest(unittest.TestCase):
    def test_matches_name(self):
        self.assertEqual([t["name"] for t in search("orders")], ["dwd_orders"])

    def test_description_not_searched(self):
        self.assertEqual(search("订单明细"), [])

    def test_case_sensitive(self):
        self.assertEqual(search("DWD"), [])

    def test_no_score(self):
        self.assertEqual(set(search("orders")[0].keys()), {"name", "desc", "fields"})
