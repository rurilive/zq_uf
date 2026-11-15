import unittest

from scrollload import done, scroll


class ScrollTest(unittest.TestCase):
    def test_always_three_rounds(self):
        self.assertEqual(scroll({}, lambda: [])["rounds"], 3)

    def test_added_counts_items(self):
        self.assertEqual(scroll({}, lambda: [1, 2])["added"], 6)

    def test_done_is_false(self):
        self.assertFalse(done({})["done"])
