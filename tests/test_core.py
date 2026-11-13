import os
import tempfile
import unittest

from csvexport import export


class CsvexportTest(unittest.TestCase):
    def test_writes_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            export([["a", "b"], [1, 2]], path)
            self.assertEqual(open(path).read().splitlines(), ["a,b", "1,2"])

    def test_encoding_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            export([["中文"]], path, {"encoding": "utf-8"})
            self.assertIn("中文", open(path, encoding="utf-8").read())
