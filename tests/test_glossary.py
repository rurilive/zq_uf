import unittest

from glossary import define, lookup, reset


class GlossaryTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_exact_lookup(self):
        define({"name": "GMV", "definition": "成交总额"})
        self.assertEqual(lookup("GMV")["definition"], "成交总额")

    def test_alias_not_found(self):
        define({"name": "GMV", "definition": "成交总额", "aliases": ["成交额"]})
        self.assertIsNone(lookup("成交额"))

    def test_redefine_overwrites_history(self):
        define({"name": "GMV", "definition": "v1"})
        define({"name": "GMV", "definition": "v2"})
        self.assertEqual(lookup("GMV")["definition"], "v2")
        self.assertNotIn("history", lookup("GMV"))
