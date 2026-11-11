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


class NormalizationTest(unittest.TestCase):
    def test_double_percent_encoding_is_folded(self):
        rules = [Rule("admin", path_prefix="/admin")]
        for url in (
            "/adm%2569n/users",
            "/%2561dmin/users",
            "/%252e%252e/admin/users",
        ):
            with self.subTest(url=url):
                result = inspect_request(Request("GET", url), rules)
                self.assertFalse(result["allowed"])
                self.assertEqual(result["rule_id"], "admin")
                self.assertEqual(result["evidence"]["normalized"]["path"], "/admin/users")

    def test_unicode_escape_is_folded(self):
        rules = [Rule("admin", path_prefix="/admin")]
        result = inspect_request(Request("GET", "/\\u0061dmin/users"), rules)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["evidence"]["normalized"]["path"], "/admin/users")

    def test_path_folding_duplicate_slashes_and_case(self):
        rules = [Rule("admin", path_prefix="/admin")]
        blocked = (
            "/ADMIN/../admin//users",
            "//admin///users",
            "/public/../admin/x",
            "/admin/./users",
        )
        for url in blocked:
            with self.subTest(url=url):
                result = inspect_request(Request("GET", url), rules)
                self.assertFalse(result["allowed"])
        result = inspect_request(Request("GET", "/public/../ok"), rules)
        self.assertTrue(result["allowed"])

    def test_original_fragment_is_preserved(self):
        rules = [Rule("admin", path_prefix="/admin")]
        result = inspect_request(Request("GET", "/adm%2569n/users"), rules)
        self.assertEqual(result["evidence"]["match"]["original_fragment"], "/adm%2569n/users")
        self.assertEqual(result["evidence"]["match"]["normalized_fragment"], "/admin/users")


class ParameterTest(unittest.TestCase):
    def test_duplicate_query_values_are_all_inspected(self):
        rules = [Rule("danger", query={"cmd": "rm"})]
        result = inspect_request(Request("GET", "/run?cmd=ls&cmd=rm&cmd=true"), rules)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "danger")
        self.assertEqual(result["evidence"]["match"]["original_fragment"], "cmd=rm")

    def test_overridden_query_value_cannot_hide(self):
        rules = [Rule("danger", query={"mode": "admin"})]
        result = inspect_request(Request("GET", "/x?mode=user&mode=admin&mode=user"), rules)
        self.assertFalse(result["allowed"])

    def test_encoded_duplicate_parameter_name_is_merged(self):
        rules = [Rule("danger", query={"cmd": "rm"})]
        result = inspect_request(Request("GET", "/x?cmd=ls&c%6dd=rm"), rules)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["evidence"]["normalized"]["query"]["cmd"], ["ls", "rm"])

    def test_duplicate_form_values_are_all_inspected(self):
        rules = [Rule("danger", body_field="cmd", body_value="rm")]
        result = inspect_request(
            Request(
                "POST",
                "/x",
                {"Content-Type": "application/x-www-form-urlencoded"},
                body=b"cmd=ls&cmd=rm",
            ),
            rules,
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "danger")


class BodyTest(unittest.TestCase):
    def test_json_is_checked_recursively(self):
        rules = [Rule("nested", body_fields={"users.0.role": "admin"})]
        body = b'{"users":[{"role":"admin"},{"role":"user"}],"meta":{"deep":{"x":"bad"}}}'
        result = inspect_request(
            Request("POST", "/x", {"Content-Type": "application/json"}, body), rules
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "nested")

        nested_rules = [Rule("deep", body_field="meta.deep.x", body_value="bad")]
        self.assertFalse(
            inspect_request(
                Request("POST", "/x", {"Content-Type": "application/json"}, body),
                nested_rules,
            )["allowed"]
        )

    def test_chunked_json_body_is_reassembled(self):
        chunks = [b'{"cmd":', b'"rm -rf /",', b'"nested":{"role":"admin"}}']
        rules = [Rule("danger", body_field="nested.role", body_value="admin")]
        result = inspect_request(
            Request("POST", "/x", {"Content-Type": "application/json"}, chunks), rules
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "danger")
        self.assertEqual(result["evidence"]["normalized"]["body"]["kind"], "json")

    def test_wire_chunked_json_body_is_reassembled(self):
        payload = b'{"cmd":"rm -rf /"}'
        wire = b""
        for index in range(0, len(payload), 4):
            chunk = payload[index : index + 4]
            wire += ("%x\r\n" % len(chunk)).encode() + chunk + b"\r\n"
        wire += b"0\r\n\r\n"
        rules = [Rule("danger", needle="rm -rf")]
        result = inspect_request(
            Request(
                "POST",
                "/x",
                {"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
                wire,
            ),
            rules,
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "danger")

    def test_malformed_json_is_rejected(self):
        rules = [Rule("admin", path_prefix="/admin")]
        result = inspect_request(
            Request("POST", "/ok", {"Content-Type": "application/json"}, b'{"a":'),
            rules,
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "body-parse-error")
        self.assertIn("reason", result["evidence"])

    def test_malformed_json_is_rejected_without_rules(self):
        result = inspect_request(
            Request("POST", "/ok", {"Content-Type": "application/json"}, b"{oops}"), []
        )
        self.assertFalse(result["allowed"])
        self.assertIsNotNone(result["rule_id"])

    def test_plain_text_and_json_duplicate_keys(self):
        rules = [Rule("needle", needle="password")]
        result = inspect_request(
            Request("POST", "/x", {"Content-Type": "text/plain"}, b"hello password"), rules
        )
        self.assertFalse(result["allowed"])

        dup = Request(
            "POST", "/x", {"Content-Type": "application/json"}, b'{"role":"user","role":"admin"}'
        )
        self.assertFalse(
            inspect_request(dup, [Rule("dup", body_field="role", body_value="admin")])["allowed"]
        )


class RuleCombinationTest(unittest.TestCase):
    def test_combined_conditions_all_must_match(self):
        rule = Rule(
            "combo",
            path_prefix="/api",
            method="POST",
            headers={"X-Tool": "scanner"},
            query={"cmd": "drop"},
            body_field="action",
            body_value="purge",
        )
        matching = Request(
            "POST",
            "/api?cmd=drop",
            {"X-Tool": "scanner", "Content-Type": "application/json"},
            b'{"action":"purge"}',
        )
        self.assertFalse(inspect_request(matching, [rule])["allowed"])

        for changed in (
            Request("GET", "/api?cmd=drop", {"X-Tool": "scanner"}, b'{"action":"purge"}'),
            Request("POST", "/other?cmd=drop", {"X-Tool": "scanner"}, b'{"action":"purge"}'),
            Request("POST", "/api?cmd=drop", {"X-Tool": "other"}, b'{"action":"purge"}'),
            Request("POST", "/api?cmd=other", {"X-Tool": "scanner"}, b'{"action":"purge"}'),
            Request(
                "POST",
                "/api?cmd=drop",
                {"X-Tool": "scanner", "Content-Type": "application/json"},
                b'{"action":"keep"}',
            ),
        ):
            with self.subTest(request=changed):
                self.assertTrue(inspect_request(changed, [rule])["allowed"])

    def test_index_keeps_rule_order_and_filters(self):
        rules = [
            Rule("method-other", method="POST", path_prefix="/x"),
            Rule("path-other", path_prefix="/y"),
            Rule("winner", method="GET", path_prefix="/x"),
        ]
        result = inspect_request(Request("GET", "/x"), rules)
        self.assertEqual(result["rule_id"], "winner")

