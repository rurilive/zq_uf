import unittest

from schemagate import apply, check


class GateTest(unittest.TestCase):
    def test_check_allows_everything(self):
        self.assertTrue(check({"a": "int"}, {"a": "text"})["allowed"])

    def test_check_has_no_breaking(self):
        self.assertEqual(check({}, {})["breaking"], [])

    def test_apply_has_no_gray(self):
        self.assertEqual(apply({"tables": ["t1"]})["gray"], [])
