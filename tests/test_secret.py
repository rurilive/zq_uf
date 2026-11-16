import unittest

from keyleak import report, reset, scan, triage, whitelist
from keyleak.detector import shannon_entropy


class DetectionTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_contextual_password_assignment_is_found(self):
        # 值本身不随机（形态规则不报），全靠 password 上下文作为低可信线索。
        entries = scan({"web/app.py": 'password = "frontdoor-lock-27"\n'})["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["value"], "frontdoor-lock-27")
        self.assertEqual(entries[0]["confidence"], "low")
        self.assertEqual(entries[0]["type"], "contextual_secret")

    def test_comment_with_keyword_is_still_reported(self):
        # 旧实现会报注释；保留该行为，注释行仍按上下文线索报低可信。
        entries = scan({"web/app.py": "# password = old\n"})["entries"]
        self.assertEqual(len(entries), 1)

    def test_high_entropy_value_without_keyword_is_now_found(self):
        # 旧实现的核心缺陷：没有关键字就漏报高熵密钥。
        paths = {"web/fixture.py": 'blob = "aB3xK9mQ2pL7vN4rT8wZ"\n'}
        entries = scan(paths)["entries"]
        self.assertEqual(len(entries), 1)
        self.assertIn(entries[0]["confidence"], ("medium", "high"))

    def test_known_prefix_is_high_confidence(self):
        paths = {"ops/deploy.yml": 'key: "AKIAIOSFODNN7EXAMPLE"\n'}
        entries = scan(paths)["entries"]
        self.assertEqual(entries[0]["confidence"], "high")
        self.assertEqual(entries[0]["type"], "aws_access_key_id")

    def test_most_specific_prefix_wins(self):
        paths = {"web/a.py": 'k = "sk-ant-api03-' + "x" * 30 + '"\n'}
        entries = scan(paths)["entries"]
        self.assertEqual(entries[0]["type"], "anthropic_api_key")

    def test_pem_private_key_is_one_high_confidence_entry(self):
        pem = (
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIBVwIBADANBgkqhkiG9w0BAQEFAASjADCBogIBAAJBAKj\n"
            "-----END PRIVATE KEY-----\n"
        )
        entries = scan({"web/key.pem": pem})["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["type"], "pem_private_key")

    def test_placeholders_and_ordinary_strings_are_ignored(self):
        paths = {
            "web/cfg.py": (
                'token = "${API_TOKEN}"\n'
                'password = "changeme"\n'
                'version = "1.2.3"\n'
                'path = "src/foo/bar.py"\n'
                'word = "thisisjustalongenglishword"\n'
                'repeat = "aaaaaaaaaaaaaaaaaaaaaaaa"\n'
            )
        }
        self.assertEqual(scan(paths)["entries"], [])

    def test_shannon_entropy_basic(self):
        self.assertEqual(shannon_entropy(""), 0.0)
        self.assertEqual(shannon_entropy("aaaa"), 0.0)
        self.assertGreater(shannon_entropy("aB3xK9mQ2pL7vN4rT8wZ"), 3.0)

    def test_url_uuid_and_version_are_not_secrets(self):
        paths = {
            "web/misc.py": (
                'u = "https://example.com/path?x=1&y=2"\n'
                'id = "550e8400-e29b-41d4-a716-446655440000"\n'
                'v = "10.20.30.40"\n'
            )
        }
        self.assertEqual(scan(paths)["entries"], [])

    def test_bare_hex_is_low_but_context_named_hex_is_high(self):
        paths = {
            "web/hash.py": 'commit = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"\n',
            "web/key.py": 'api_key = "ffeeddccbbaa99887766554433221100ffeeddcc"\n',
        }
        entries = {e["context"]: e for e in scan(paths)["entries"]}
        self.assertEqual(entries[None]["confidence"], "low")
        self.assertEqual(entries["api_key"]["confidence"], "high")

    def test_bare_high_entropy_token_is_detected_without_assignment(self):
        paths = {"svc/y.log": "noise before aB3xK9mQ2pL7vN4rT8wZ after\n"}
        entries = scan(paths)["entries"]
        self.assertEqual(len(entries), 1)


class MergeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_same_secret_in_two_repos_is_merged_with_locations(self):
        paths = {
            "web/a.py": 'api_key = "samevalue"\n',
            "ops/b.py": 'api_key = "samevalue"\n',
        }
        entries = scan(paths)["entries"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(len(entry["locations"]), 2)
        self.assertEqual(sorted(entry["repos"]), ["ops", "web"])
        self.assertEqual(report()["duplicates"], 1)

    def test_locations_distinguish_code_config_build(self):
        paths = {
            "ops/conf.yaml": 'api_key: "AKIAIOSFODNN7EXAMPLE"\n',
            "web/app.py": 'k = "AKIAIOSFODNN7EXAMPLE"\n',
            "web/dist/bundle.js": 't="AKIAIOSFODNN7EXAMPLE"\n',
        }
        entry = scan(paths)["entries"][0]
        kinds = {loc["file"].split("/")[-1]: loc["kind"] for loc in entry["locations"]}
        self.assertEqual(kinds["conf.yaml"], "config")
        self.assertEqual(kinds["app.py"], "code")
        self.assertEqual(kinds["bundle.js"], "build")

    def test_bare_reference_is_usage_trace(self):
        paths = {
            "web/a.py": 'token = "samevalue"\n',
            "ops/b.py": "client.call(samevalue)\n",
        }
        entry = scan(paths)["entries"][0]
        kinds = {u["kind"] for u in entry["usage"]}
        self.assertEqual(kinds, {"definition", "call"})


class StatusTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_in_use_secret_is_flagged(self):
        paths = {
            "web/a.py": 'token = "samevalue"\n',
            "ops/b.py": "client.call(samevalue)\n",
        }
        entry = scan(paths)["entries"][0]
        self.assertTrue(entry["in_use"])
        self.assertEqual(entry["status"], "in_use")

    def test_single_definition_stays_open(self):
        entry = scan({"web/a.py": 'token = "abcdefghijklmnop"\n'})["entries"][0]
        self.assertFalse(entry["in_use"])
        self.assertEqual(entry["status"], "open")

    def test_whitelist_by_value_hides_entry(self):
        paths = {"web/a.py": 'token = "abcdefghijklmnop"\n'}
        whitelist(value="abcdefghijklmnop")
        self.assertEqual(scan(paths)["entries"], [])
        self.assertEqual(report()["whitelisted"], 0)

    def test_whitelist_pattern_matches_existing_entries(self):
        paths = {"web/a.py": 'k = "sk-ant-api03-' + "z" * 30 + '"\n'}
        scan(paths)
        whitelist(pattern=r"sk-ant-.*")
        body = report()
        self.assertEqual(body["whitelisted"], 1)
        self.assertEqual(body["total"], 1)

    def test_triage_records_handler_and_method(self):
        entry = scan({"web/a.py": 'token = "abcdefghijklmnop"\n'})["entries"][0]
        triage(entry["id"], "alice", "rotated", note="moved to vault")
        body = report()
        self.assertEqual(body["resolved"], 1)
        resolved = body["entries"][0]
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["handler"], "alice")
        self.assertEqual(resolved["method"], "rotated")

    def test_triage_unknown_id_raises(self):
        with self.assertRaises(KeyError):
            triage("missing", "alice", "rotated")


class ReportTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_report_groups_by_repo(self):
        paths = {
            "web/app.py": 'password = "frontdoor-lock-27"\n',
            "ops/deploy.yml": 'token: "aB3xK9mQ2pL7vN4rT8wZ"\n',
        }
        scan(paths)
        body = report()
        self.assertEqual(body["repos"], {"ops": 1, "web": 1})
        self.assertIn("repo_groups", body)
        self.assertEqual(body["total"], 2)
        self.assertIn("open", body)

    def test_value_is_masked_in_output(self):
        entry = scan({"web/a.py": 'token = "abcdefghijklmnop"\n'})["entries"][0]
        self.assertNotIn("abcdefghijklmnop", entry["masked"])
        self.assertTrue(entry["masked"].startswith("abcd"))


class InputValidationTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_paths_must_be_dict(self):
        with self.assertRaises(ValueError):
            scan(["web/a.py"])

    def test_file_name_must_be_non_empty_str(self):
        with self.assertRaises(ValueError):
            scan({"": "x"})

    def test_content_must_be_str(self):
        with self.assertRaises(ValueError):
            scan({"web/a.py": 123})


if __name__ == "__main__":
    unittest.main()
