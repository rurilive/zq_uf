import unittest

from linkrewrite import rewrite, rules


class RewriteTest(unittest.TestCase):
    def test_rewrite_returns_input(self):
        result = rewrite("https://a/p?utm_source=x&page=2", "a")
        self.assertEqual(result["after"], result["before"])

    def test_no_rule_hit(self):
        self.assertEqual(rewrite("https://a/p?page=2", "a")["hit"], [])

    def test_rules_empty(self):
        self.assertEqual(rules("a")["rules"], [])
