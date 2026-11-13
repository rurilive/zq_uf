import unittest

from dqrules import run


class DqrulesTest(unittest.TestCase):
    def test_required_pass(self):
        rows = [{"id": "1", "name": "a"}]
        result = run(rows, {"required": ["id", "name"]})
        self.assertEqual(result["failed"], [])

    def test_missing_field(self):
        result = run([{"id": "1"}], {"required": ["id", "name"]})
        self.assertEqual(result["failed"][0]["missing"], ["name"])

    def test_empty_dataset(self):
        self.assertEqual(run([], {"required": ["id"]}), {"passed": [], "failed": []})
