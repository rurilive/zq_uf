import importlib
import unittest

from tablearchive import archive, query

module = importlib.import_module("tablearchive.archive")


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        module._rows.clear()

    def test_archive_zero(self):
        self.assertEqual(archive("t", {"before": 2020})["archived"], 0)

    def test_query_filters(self):
        module._rows.extend([{"city": "sh"}, {"city": "bj"}])
        self.assertEqual(len(query("t", {"city": "sh"})), 1)

    def test_query_empty(self):
        self.assertEqual(query("t", {}), [])
