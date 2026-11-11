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

    def test_double_url_encoding_is_normalized(self):
        rules = [Rule("admin", path_prefix="/admin")]
        result = inspect_request(Request("GET", "/%2561dmin/users"), rules)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "admin")
        self.assertEqual(
            result["evidence"]["normalized"]["path"], "/admin/users"
        )
        self.assertEqual(result["evidence"]["url"], "/%2561dmin/users")

    def test_unicode_escape_is_normalized(self):
        rules = [Rule("secret", needle="password")]
        body = b'{"field":"PASSW\\u006fRD"}'
        result = inspect_request(
            Request(
                "POST",
                "/x",
                {"Content-Type": "application/json"},
                body,
            ),
            rules,
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "secret")

    def test_repeated_query_params_are_all_checked(self):
        rules = [Rule("xss", query_param="q=<script>")]
        request = Request(
            "GET", "/search?q=safe&q=%3Cscript%3E&q=fine"
        )
        result = inspect_request(request, rules)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "xss")
        match = result["evidence"]["matches"][0]
        self.assertEqual(match["location"], "query")
        self.assertEqual(match["index"], 1)
        self.assertEqual(match["original"], "%3Cscript%3E")
        self.assertEqual(match["normalized"], "q=<script>")

    def test_path_folding_normalizes_dots_and_slashes(self):
        rules = [Rule("admin", path_prefix="/admin")]
        request = Request("GET", "//admin/../admin//users")
        result = inspect_request(request, rules)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "admin")
        self.assertEqual(
            result["evidence"]["normalized"]["path"], "/admin/users"
        )

    def test_chunked_json_is_reassembled_before_inspection(self):
        payload = b'{"user":{"role":"admin"}}'
        first = payload[:9]
        second = payload[9:]
        chunked = (
            f"{len(first):x}\r\n".encode()
            + first
            + b"\r\n"
            + f"{len(second):x}\r\n".encode()
            + second
            + b"\r\n0\r\n\r\n"
        )
        request = Request(
            "POST",
            "/api",
            {
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
            },
            chunked,
        )
        result = inspect_request(request, [Rule("role", body_field="role=admin")])
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "role")
        self.assertEqual(
            result["evidence"]["body"], b'{"user":{"role":"admin"}}'.decode()
        )
        match = result["evidence"]["matches"][0]
        self.assertEqual(match["field"], "user.role")
        self.assertEqual(match["normalized"], "admin")

    def test_malformed_json_is_denied(self):
        request = Request(
            "POST",
            "/api",
            {"Content-Type": "application/json"},
            b'{"role":',
        )
        result = inspect_request(request, [])
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "malformed-body")
        self.assertTrue(result["evidence"]["reason"])

    def test_malformed_chunked_framing_is_denied(self):
        request = Request(
            "POST",
            "/api",
            {"Transfer-Encoding": "chunked"},
            b"ZZ\r\nxx\r\n",
        )
        result = inspect_request(request, [])
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "malformed-body")

    def test_nested_json_arrays_are_checked_recursively(self):
        body = b'{"items":[{"name":"ok"},{"name":"EVIL"}]}'
        request = Request(
            "POST",
            "/api",
            {"Content-Type": "application/json"},
            body,
        )
        result = inspect_request(request, [Rule("nested", body_field="name=evil")])
        self.assertFalse(result["allowed"])
        self.assertEqual(result["rule_id"], "nested")
        self.assertEqual(result["evidence"]["matches"][0]["field"], "items[1].name")

    def test_form_body_repeats_and_plain_text_are_checked(self):
        form_request = Request(
            "POST",
            "/login",
            {"Content-Type": "application/x-www-form-urlencoded"},
            b"user=a&password=secret",
        )
        result = inspect_request(
            form_request, [Rule("form", body_field="password=secret")]
        )
        self.assertFalse(result["allowed"])

        text_request = Request("POST", "/note", body=b"plain password leak")
        result = inspect_request(text_request, [Rule("text", needle="password")])
        self.assertFalse(result["allowed"])

    def test_combined_conditions_must_all_match(self):
        rule = Rule(
            "combo",
            method="POST",
            path_prefix="/api",
            header="X-Token: abc",
            body_field="role=admin",
        )
        headers = {
            "Content-Type": "application/json",
            "X-Token": "zzzABCyyy",
        }
        matched = inspect_request(
            Request("POST", "/api/v1", headers, b'{"role":"admin"}'), [rule]
        )
        self.assertFalse(matched["allowed"])
        self.assertEqual(matched["rule_id"], "combo")

        rejected = inspect_request(
            Request(
                "POST",
                "/api/v1",
                {"Content-Type": "application/json", "X-Token": "noop"},
                b'{"role":"admin"}',
            ),
            [rule],
        )
        self.assertTrue(rejected["allowed"])
