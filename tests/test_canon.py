import unittest

from htmlcanon import canonical, same


class CanonTest(unittest.TestCase):
    def test_strip(self):
        self.assertEqual(canonical(" <p>a</p> "), "<p>a</p>")

    def test_same_after_strip(self):
        self.assertTrue(same("<p>a</p>", " <p>a</p> "))

    def test_attr_order_differs(self):
        self.assertFalse(same('<p id="1" class="x">a</p>', '<p class="x" id="1">a</p>'))
