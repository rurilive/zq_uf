import unittest

from confdrift import diff, merge_alerts, snapshot


class DriftTest(unittest.TestCase):
    def test_value_change(self):
        base = snapshot("prod", {"workers": 4})
        target = snapshot("prod", {"workers": 8})
        self.assertEqual(diff(base, target)["changed"][0]["key"], "workers")

    def test_added_key_ignored(self):
        base = snapshot("prod", {"workers": 4})
        target = snapshot("prod", {"workers": 4, "token": "abc"})
        self.assertEqual(diff(base, target)["changed"], [])

    def test_sensitive_plain(self):
        base = snapshot("prod", {"token": "abc"})
        target = snapshot("prod", {"token": "xyz"})
        self.assertEqual(diff(base, target)["changed"][0]["from"], "abc")


class FingerprintTest(unittest.TestCase):
    def test_same_config_same_fingerprint(self):
        first = snapshot("prod", {"workers": 4, "token": "abc"}, sensitive=["token"])
        second = snapshot("staging", {"token": "abc", "workers": 4}, sensitive=["token"])
        self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_drift_breaks_fingerprint(self):
        base = snapshot("prod", {"workers": 4})
        target = snapshot("prod", {"workers": 8})
        self.assertNotEqual(base["fingerprint"], target["fingerprint"])
        self.assertFalse(diff(base, target)["fingerprint_match"])

    def test_fingerprint_hides_sensitive_value(self):
        snap = snapshot("prod", {"token": "abc"}, sensitive=["token"])
        self.assertNotIn("abc", snap["fingerprint"])
        self.assertEqual(snap["config"]["token"], "abc")


class DiffKindTest(unittest.TestCase):
    def setUp(self):
        self.base = snapshot("repo", {"keep": 1, "gone": 2, "typed": 3})
        self.target = snapshot("prod", {"keep": 1, "typed": "3", "new": 9})
        self.result = diff(self.base, self.target)

    def test_added(self):
        self.assertEqual([e["key"] for e in self.result["added"]], ["new"])
        self.assertEqual(self.result["added"][0]["source"], "target_only")

    def test_removed(self):
        self.assertEqual([e["key"] for e in self.result["removed"]], ["gone"])
        self.assertEqual(self.result["removed"][0]["source"], "base_only")

    def test_type_changed(self):
        entry = self.result["type_changed"][0]
        self.assertEqual(entry["key"], "typed")
        self.assertEqual((entry["from_type"], entry["to_type"]), ("int", "str"))

    def test_unchanged_key_not_reported(self):
        keys = [a["key"] for a in self.result["alerts"]]
        self.assertNotIn("keep", keys)


class SeverityTest(unittest.TestCase):
    def test_grading_by_kind(self):
        base = snapshot("repo", {"a": 1, "b": 1, "c": 1})
        target = snapshot("prod", {"a": 2, "b": "1", "d": 1})
        result = diff(base, target)
        self.assertEqual(result["changed"][0]["severity"], "medium")
        self.assertEqual(result["type_changed"][0]["severity"], "high")
        self.assertEqual(result["removed"][0]["severity"], "high")
        self.assertEqual(result["added"][0]["severity"], "low")
        self.assertEqual(result["severity"], "high")

    def test_sensitive_change_is_critical(self):
        base = snapshot("repo", {"token": "abc"}, sensitive=["token"])
        target = snapshot("prod", {"token": "xyz"}, sensitive=["token"])
        result = diff(base, target)
        self.assertEqual(result["changed"][0]["severity"], "critical")
        self.assertEqual(result["severity"], "critical")

    def test_no_drift_severity_none(self):
        snap = snapshot("prod", {"workers": 4})
        self.assertEqual(diff(snap, snapshot("prod", {"workers": 4}))["severity"], "none")


class SensitiveTest(unittest.TestCase):
    def test_sensitive_change_masked(self):
        base = snapshot("repo", {"token": "abc"}, sensitive=["token"])
        target = snapshot("prod", {"token": "xyz"}, sensitive=["token"])
        entry = diff(base, target)["changed"][0]
        self.assertEqual(entry["from"], "***")
        self.assertEqual(entry["to"], "***")

    def test_sensitive_same_hash_no_drift(self):
        base = snapshot("repo", {"token": "abc"}, sensitive=["token"])
        target = snapshot("prod", {"token": "abc"}, sensitive=["token"])
        self.assertEqual(diff(base, target)["changed"], [])

    def test_sensitive_added_masked(self):
        base = snapshot("repo", {}, sensitive=["token"])
        target = snapshot("prod", {"token": "abc"}, sensitive=["token"])
        self.assertEqual(diff(base, target)["added"][0]["to"], "***")


class MultiEnvTest(unittest.TestCase):
    def test_cross_env_diff_reports_both_envs(self):
        baseline = snapshot("repo-baseline", {"workers": 4})
        prod = snapshot("prod", {"workers": 8})
        result = diff(baseline, prod)
        self.assertEqual(result["base_env"], "repo-baseline")
        self.assertEqual(result["env"], "prod")
        self.assertEqual(result["alerts"][0]["env"], "prod")

    def test_suggestion_and_source_present(self):
        base = snapshot("repo", {"workers": 4})
        target = snapshot("prod", {"workers": 8})
        alert = diff(base, target)["alerts"][0]
        self.assertEqual(alert["source"], "both")
        self.assertIn("基线", alert["suggestion"])

    def test_repeated_drift_merged(self):
        baseline = snapshot("repo", {"workers": 4})
        prod = snapshot("prod", {"workers": 8})
        staging = snapshot("staging", {"workers": 8})
        merged = merge_alerts(diff(baseline, prod), diff(baseline, staging), diff(baseline, prod))
        prod_alert = [a for a in merged if a["env"] == "prod"][0]
        self.assertEqual(prod_alert["count"], 2)
        self.assertEqual(len(merged), 2)
