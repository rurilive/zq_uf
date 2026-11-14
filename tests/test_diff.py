import unittest

from schemadiff import diff, impact


class DiffTest(unittest.TestCase):
    def test_added_removed(self):
        result = diff({"fields": [{"name": "a"}]}, {"fields": [{"name": "b"}]})
        self.assertEqual(result["added"], ["b"])

    def test_type_change_ignored(self):
        old = {"fields": [{"name": "a", "type": "str"}]}
        new = {"fields": [{"name": "a", "type": "int"}]}
        self.assertEqual(diff(old, new)["added"], [])

    def test_impact_empty(self):
        self.assertEqual(impact({"added": ["a"]}), [])
