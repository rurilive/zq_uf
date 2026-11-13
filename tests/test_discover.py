import unittest

from formdiscover import discover


PAGE = {
    "action": "/login",
    "method": "POST",
    "inputs": [
        {"name": "user", "type": "text"},
        {"name": "pwd", "type": "password"},
        {"name": "csrf", "type": "hidden", "value": "t1"},
        {"name": "go", "type": "submit"},
    ],
}


class DiscoverTest(unittest.TestCase):
    def test_visible_fields_only(self):
        result = discover(PAGE)
        self.assertEqual([f["name"] for f in result["fields"]], ["user", "pwd"])

    def test_action_and_method(self):
        result = discover(PAGE)
        self.assertEqual(result["action"], "/login")
        self.assertEqual(result["method"], "post")

    def test_single_step(self):
        self.assertNotIn("steps", discover(PAGE))
