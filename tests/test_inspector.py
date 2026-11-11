import unittest

from guardwire import Request, Rule, inspect_request


class InspectorTest(unittest.TestCase):
    def test_default_allow(self):
        result = inspect_request(Request("GET", "/ok"), [])
        self.assertTrue(result["allowed"])
        self.assertIsNone(result["rule_id"])

    def test_path_rule(self):
        rules = [Rule("admin", path_prefix="/admin")]
        result = inspect_request(Request("GET", "/admin/users"), rules)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "admin")

    def test_method_filter(self):
        rules = [Rule("post-only", method="POST", path_prefix="/admin")]
        result = inspect_request(Request("GET", "/admin"), rules)
        self.assertTrue(result["allowed"])

    def test_body_needle(self):
        rules = [Rule("secret", needle="password")]
        result = inspect_request(Request("POST", "/login", body=b"password=1"), rules)
        self.assertFalse(result["allowed"])
