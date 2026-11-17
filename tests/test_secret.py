import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from keyleak import add_whitelist, report, reset, resolve, scan  # noqa: E402


class ScanTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_keyword_assignment_is_found_with_low_confidence(self):
        entries = scan({"web/app.py": 'password = "hunter2"\n'})["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["value"], "hunter2")
        self.assertEqual(entries[0]["confidence"], "low")
        self.assertEqual(entries[0]["type"], "generic-secret")

    def test_high_entropy_value_without_keyword_is_found(self):
        paths = {"web/fixture.py": 'blob = "aB3xK9mQ2pL7vN4rT8wZ"\n'}
        entries = scan(paths)["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["confidence"], "medium")

    def test_known_prefix_is_high_confidence(self):
        paths = {"ops/aws.sh": 'export AWS_KEY="AKIAIOSFODNN7EXAMPLE"\n'}
        entry = scan(paths)["entries"][0]
        self.assertEqual(entry["confidence"], "high")
        self.assertEqual(entry["type"], "aws-access-key-id")

    def test_jwt_and_private_key_are_detected(self):
        paths = {
            "svc/auth.py": (
                'jwt = "eyJhbGciOiJIUzI1NiJ9.'
                'eyJzdWIiOiIxMjM0NTY3ODkwIn0.'
                'SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"\n'
                "key = \"\"\"-----BEGIN PRIVATE KEY-----\n"
                "MIIBVwIBADANBgkqhkiG9w0BAQEFAASCAUEwggE9\n"
                "-----END PRIVATE KEY-----\"\"\"\n"
            )
        }
        types = {e["type"] for e in scan(paths)["entries"]}
        self.assertIn("jwt", types)
        self.assertIn("private-key", types)

    def test_placeholder_and_template_are_ignored(self):
        paths = {
            "web/app.py": (
                'password = "changeme"\n'
                'token = "${SERVICE_TOKEN}"\n'
                'api_key: "{{ .Values.apiKey }}"\n'
            )
        }
        self.assertEqual(scan(paths)["entries"], [])

    def test_comment_with_keyword_is_still_reported(self):
        entries = scan({"web/app.py": '# password = "oldcred12"\n'})["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["locations"][0]["line"], 1)

    def test_same_secret_is_merged_with_all_locations(self):
        paths = {
            "web/a.py": 'api_key = "samevalue"\n',
            "ops/b.yml": "api_key: samevalue\n",
        }
        result = scan(paths)
        self.assertEqual(len(result["entries"]), 1)
        entry = result["entries"][0]
        self.assertEqual(len(entry["locations"]), 2)
        files = {loc["file"] for loc in entry["locations"]}
        self.assertEqual(files, {"web/a.py", "ops/b.yml"})
        self.assertEqual(report()["duplicates"], 1)

    def test_location_kinds_are_classified(self):
        paths = {
            "web/app.py": 'token = "aB3xK9mQ2pL7vN4rT8wZ"\n',
            "ops/deploy.yml": 'token: "aB3xK9mQ2pL7vN4rT8wZ"\n',
            "dist/bundle.min.js": 't="aB3xK9mQ2pL7vN4rT8wZ";\n',
        }
        entry = scan(paths)["entries"][0]
        kinds = {loc["kind"] for loc in entry["locations"]}
        self.assertEqual(kinds, {"code", "config", "build"})

    def test_value_referenced_again_leaves_usage_trace(self):
        paths = {
            "web/app.py": (
                'token = "aB3xK9mQ2pL7vN4rT8wZ"\n'
                'header = "aB3xK9mQ2pL7vN4rT8wZ"\n'
            )
        }
        entry = scan(paths)["entries"][0]
        self.assertTrue(entry["in_use"])
        self.assertTrue(entry["usage"])
        self.assertTrue(any(u["evidence"] == "reference" for u in entry["usage"]))

    def test_build_artifact_secret_is_marked_in_use(self):
        paths = {"dist/bundle.min.js": 't="aB3xK9mQ2pL7vN4rT8wZ";\n'}
        entry = scan(paths)["entries"][0]
        self.assertTrue(entry["in_use"])

    def test_whitelist_value_is_excluded_and_persists(self):
        add_whitelist(["hunter2"])
        paths = {"web/app.py": 'password = "hunter2"\n'}
        self.assertEqual(scan(paths)["entries"], [])
        # 白名单是跨扫描持久状态，重新扫描仍然排除。
        self.assertEqual(scan(paths)["entries"], [])
        self.assertEqual(report()["whitelisted"], 1)

    def test_whitelist_can_use_entry_id(self):
        entry = scan({"web/app.py": 'password = "hunter2"\n'})["entries"][0]
        add_whitelist([entry["entry_id"]])
        self.assertEqual(scan({"web/app.py": 'password = "hunter2"\n'})["entries"], [])

    def test_scan_whitelist_argument(self):
        paths = {"web/app.py": 'password = "hunter2"\n'}
        self.assertEqual(scan(paths, whitelist=["hunter2"])["entries"], [])
        self.assertEqual(scan(paths)["entries"], [])

    def test_resolve_records_owner_and_method(self):
        entry_id = scan({"web/app.py": 'password = "hunter2"\n'})["entries"][0]["entry_id"]
        resolve(entry_id, "alice", "rotated")
        body = report()
        self.assertEqual(body["resolved"], 1)
        self.assertEqual(body["open"], 0)
        # 处置状态在重新扫描后仍然保留。
        entry = scan({"web/app.py": 'password = "hunter2"\n'})["entries"][0]
        self.assertEqual(entry["status"], "resolved")
        self.assertEqual(entry["handled_by"], "alice")
        self.assertEqual(entry["handle_method"], "rotated")

    def test_in_use_secrets_are_separately_counted(self):
        paths = {
            "web/a.py": 'api_key = "samevalue"\n',
            "ops/b.yml": "api_key: samevalue\n",
        }
        scan(paths)
        body = report()
        self.assertEqual(body["in_use"], 1)
        self.assertEqual(body["repo_groups"]["web"]["in_use"], 1)

    def test_report_groups_by_repo_for_dispatch(self):
        paths = {
            "web/a.py": 'api_key = "samevalue"\n',
            "ops/b.yml": "api_key: samevalue\n",
            "ops/c.yml": 'password = "hunter2"\n',
        }
        scan(paths)
        body = report()
        self.assertEqual(body["repos"], {"web": 1, "ops": 2})
        groups = scan(paths)["repo_groups"]
        entry_id = [e for e in scan(paths)["entries"] if e["value"] == "samevalue"][0]["entry_id"]
        self.assertIn(entry_id, groups["web"])
        self.assertIn(entry_id, groups["ops"])

    def test_masked_value_does_not_leak_full_secret(self):
        entry = scan({"web/app.py": 'token = "aB3xK9mQ2pL7vN4rT8wZ"\n'})["entries"][0]
        self.assertNotIn("aB3xK9mQ2pL7vN4rT8wZ", entry["masked"])
        self.assertIn("***", entry["masked"])

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            scan("not a dict")
        with self.assertRaises(ValueError):
            scan({"": "x"})
        with self.assertRaises(ValueError):
            scan({"web/a.py": 123})
        with self.assertRaises(KeyError):
            resolve("key-nope", "alice", "rotated")
        with self.assertRaises(ValueError):
            add_whitelist([""])


if __name__ == "__main__":
    unittest.main()
