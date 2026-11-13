import unittest

from fieldsource import extract


def rule(field, getter):
    return {"field": field, "get": getter}


class ExtractTest(unittest.TestCase):
    def test_plain_values(self):
        values = extract({"title": "键盘"}, [rule("title", lambda p: p["title"])])
        self.assertEqual(values, {"title": "键盘"})

    def test_later_rule_overrides(self):
        rules = [rule("price", lambda p: p.get("a")), rule("price", lambda p: p.get("b"))]
        values = extract({"a": "199", "b": "299"}, rules)
        self.assertEqual(values["price"], "299")

    def test_missing_field_absent(self):
        values = extract({}, [rule("title", lambda p: p.get("title"))])
        self.assertEqual(values, {})
