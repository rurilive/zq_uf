import json
import os
import tempfile
import unittest

from harvestcfg import (
    CircularReferenceError,
    ConfigLoader,
    ConflictError,
    SensitiveValueError,
    ValidationError,
    validate_config,
    watch,
)


def write(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data))


class MergeTest(unittest.TestCase):
    def test_four_layer_merge(self):
        loader = ConfigLoader()
        loader.set_layer("default", {"rate_limit": 1, "concurrency": 1}, "default.json")
        loader.set_layer("site", {"rate_limit": 2}, "site.json")
        loader.set_layer("task", {"rate_limit": 3}, "task.json")
        loader.set_layer("tenant", {"rate_limit": 4}, "tenant.json")
        resolved = loader.resolve()
        self.assertEqual(resolved["rate_limit"], 4)
        self.assertEqual(resolved["concurrency"], 1)
        chain = resolved.source_chain("rate_limit")
        self.assertEqual([layer for layer, _ in chain],
                         ["default", "site", "task", "tenant"])

    def test_site_overrides(self):
        loader = ConfigLoader()
        loader.set_layer("default", {
            "rate_limit": 1,
            "concurrency": 1,
            "retry": {"max_attempts": 3, "backoff": 1},
            "headers": {"User-Agent": "default-ua", "Accept": "text/html"},
            "proxy_group": "default-pool",
        }, "default.json")
        loader.set_layer("site", {
            "rate_limit": 5,
            "concurrency": 8,
            "retry": {"max_attempts": 5},
            "headers": {"User-Agent": "site-ua"},
            "proxy_group": "site-pool",
        }, "site.json")
        resolved = loader.resolve()
        self.assertEqual(resolved["rate_limit"], 5)
        self.assertEqual(resolved["concurrency"], 8)
        self.assertEqual(resolved["retry"], {"max_attempts": 5, "backoff": 1})
        self.assertEqual(resolved["headers"],
                         {"User-Agent": "site-ua", "Accept": "text/html"})
        self.assertEqual(resolved["proxy_group"], "site-pool")

    def test_conflict_reports_field_and_sources(self):
        loader = ConfigLoader()
        loader.add_source("site", {"rate_limit": 2}, "a.json")
        with self.assertRaises(ConflictError) as ctx:
            loader.add_source("site", {"rate_limit": 3}, "b.json")
        message = str(ctx.exception)
        self.assertIn("rate_limit", message)
        self.assertIn("a.json", message)
        self.assertIn("b.json", message)


class ValidationTest(unittest.TestCase):
    def test_unknown_field_suggests_typo_fix(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_config({"rat_limt": 2}, "site.json")
        self.assertIn("rate_limit", str(ctx.exception))

    def test_selector_syntax(self):
        validate_config({"selectors": {"title": "div.content > h1"}})
        with self.assertRaises(ValidationError):
            validate_config({"selectors": {"title": "div[attr"}})
        with self.assertRaises(ValidationError):
            validate_config({"selectors": {"title": "div >"}})

    def test_regex_syntax(self):
        validate_config({"patterns": {"page": r"page=\d+"}})
        with self.assertRaises(ValidationError):
            validate_config({"patterns": {"page": "(unclosed"}})

    def test_sensitive_fields_reference_only(self):
        with self.assertRaises(SensitiveValueError):
            validate_config({"api_key": "plain-text-secret"})
        validate_config({"api_key": "${env:API_KEY}"})
        validate_config({"proxy_auth": "${vault:proxy/main}"})


class ExtendsTest(unittest.TestCase):
    def test_profile_inheritance(self):
        loader = ConfigLoader()
        loader.add_profile("base", {"rate_limit": 2, "concurrency": 4})
        loader.set_layer("site", {"extends": "base", "rate_limit": 6})
        resolved = loader.resolve()
        self.assertEqual(resolved["rate_limit"], 6)
        self.assertEqual(resolved["concurrency"], 4)

    def test_circular_reference(self):
        loader = ConfigLoader()
        loader.add_profile("a", {"extends": "b"})
        loader.add_profile("b", {"extends": "a"})
        loader.set_layer("site", {"extends": "a"})
        with self.assertRaises(CircularReferenceError):
            loader.resolve()

    def test_self_reference(self):
        loader = ConfigLoader()
        loader.add_profile("self", {"extends": "self"})
        loader.set_layer("site", {"extends": "self"})
        with self.assertRaises(CircularReferenceError):
            loader.resolve()


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.site = os.path.join(self.tmp.name, "site.json")
        write(os.path.join(self.tmp.name, "default.json"),
              {"rate_limit": 1, "concurrency": 2})
        write(self.site, {"rate_limit": 2})

    def test_fingerprint_skips_unchanged(self):
        watcher = watch(self.tmp.name)
        self.assertEqual(watcher.resolved["rate_limit"], 2)
        self.assertIsNone(watcher.check_once())
        write(self.site, {"rate_limit": 2})
        self.assertIsNone(watcher.check_once())

    def test_diff_preview_before_apply(self):
        watcher = watch(self.tmp.name)
        write(self.site, {"rate_limit": 5})
        preview = watcher.check_once()
        self.assertIsNotNone(preview)
        self.assertTrue(preview.applied)
        self.assertIn(("changed", "rate_limit", 2, 5), preview.changes)
        self.assertEqual(watcher.resolved["rate_limit"], 5)
        self.assertEqual(watcher.versions[-1]["status"], "applied")

    def test_invalid_update_auto_rollback(self):
        watcher = watch(self.tmp.name)
        write(self.site, {"rat_limt": 9})
        preview = watcher.check_once()
        self.assertFalse(preview.applied)
        self.assertIsNotNone(preview.error)
        self.assertEqual(watcher.resolved["rate_limit"], 2)
        self.assertEqual(watcher.versions[-1]["status"], "rejected")

    def test_manual_rollback(self):
        watcher = watch(self.tmp.name)
        write(self.site, {"rate_limit": 7})
        watcher.check_once()
        self.assertEqual(watcher.resolved["rate_limit"], 7)
        preview = watcher.rollback()
        self.assertIn(("changed", "rate_limit", 7, 2), preview.changes)
        self.assertEqual(watcher.resolved["rate_limit"], 2)
        self.assertEqual(watcher.versions[-1]["status"], "rollback")

    def test_version_history(self):
        watcher = watch(self.tmp.name)
        write(self.site, {"rate_limit": 3})
        watcher.check_once()
        statuses = [v["status"] for v in watcher.versions]
        self.assertEqual(statuses, ["applied", "applied"])
        self.assertEqual([v["version"] for v in watcher.versions], [1, 2])


if __name__ == "__main__":
    unittest.main()
