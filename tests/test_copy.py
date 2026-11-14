import importlib
import unittest

from tablecopy import copy, verify

module = importlib.import_module("tablecopy.copy")


class CopyTest(unittest.TestCase):
    def setUp(self):
        module._source.clear()
        module._target.clear()

    def test_copy_rows(self):
        self.assertEqual(copy([{"id": 1}, {"id": 2}], "dst")["rows"], 2)

    def test_copy_replaces(self):
        copy([{"id": 1}], "dst")
        copy([{"id": 2}], "dst")
        self.assertEqual(list(module._target), [2])

    def test_verify_rows(self):
        module._source.update({1: {"id": 1}})
        copy([{"id": 1}], "dst")
        self.assertTrue(verify()["ok"])
