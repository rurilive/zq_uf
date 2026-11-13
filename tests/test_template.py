import unittest

from ruletemplate import apply


class TemplateTest(unittest.TestCase):
    def test_returns_given_rules(self):
        rules = [{"name": "not_null", "field": "id"}]
        self.assertEqual(apply("orders", rules)["rules"], rules)

    def test_no_template_key(self):
        result = apply("orders", [{"name": "not_null", "field": "id"}])
        self.assertNotIn("template", result)

    def test_duplicate_names_kept(self):
        rules = [{"name": "range", "field": "qty", "max": 10},
                 {"name": "range", "field": "qty", "max": 20}]
        self.assertEqual(len(apply("orders", rules)["rules"]), 2)
