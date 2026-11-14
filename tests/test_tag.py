import importlib
import unittest

from retentiontag import apply, tag

module = importlib.import_module("retentiontag.tag")


class TagTest(unittest.TestCase):
    def setUp(self):
        module._tags.clear()

    def test_tag_stores(self):
        self.assertEqual(tag("t", "keep30")["tag"], "keep30")

    def test_tag_overwrites(self):
        tag("t", "keep30")
        tag("t", "keep7")
        self.assertEqual(module._tags["t"], "keep7")

    def test_apply_counts(self):
        tag("t", "keep7")
        self.assertEqual(apply("t")["deleted"], 1)
