import unittest

from riskrule import evaluate, explain, reset


class RulesTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_amount_rule_denies(self):
        got = evaluate({"id": "e1", "amount": 1500})
        self.assertEqual(got["action"], "deny")
        self.assertEqual(got["rule"], "r1")

    def test_no_match_allows(self):
        got = evaluate({"id": "e3", "amount": 10, "country": "JP", "channel": "app"})
        self.assertEqual(got["action"], "allow")

    def test_values_not_returned(self):
        got = evaluate({"id": "e4", "amount": 2000})
        self.assertEqual(got["values"], {})

    def test_explain_has_no_replay(self):
        got = explain("r1")
        self.assertEqual(got["threshold"], 1000)
        self.assertEqual(got["replay"], [])

    def test_event_must_be_dict(self):
        with self.assertRaises(ValueError):
            evaluate(["amount", 1500])
