import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from crawlguard import Guard, admit, reload


class GuardTest(unittest.TestCase):
    def test_everything_admitted(self):
        self.assertTrue(admit({"id": "t1", "site": "blocked"})["admitted"])

    def test_no_audit(self):
        self.assertEqual(admit({"id": "t2"})["audit"], [])

    def test_reload_is_noop(self):
        self.assertFalse(reload()["reloaded"])


class Clock:
    """可控时钟，让生效时间相关的用例稳定。"""

    def __init__(self, moment=None):
        self.moment = moment or datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.moment


class ListFileMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "list.json")
        self.clock = Clock()
        self.guard = Guard(self.path, now=self.clock)

    def write_list(self, version, *, sources=None, sites=None, effective_at=None):
        payload = {
            "version": version,
            "effective_at": effective_at
            or self.clock.moment.astimezone(timezone.utc).isoformat(),
            "sources": sources or {},
            "sites": sites or {},
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return payload


class ListValidationTest(ListFileMixin, unittest.TestCase):
    def test_allow_list_lets_only_listed_sites_in(self):
        self.write_list("v1", sources={"allow": ["cms-*"]}, sites={"allow": ["shop"]})
        self.assertTrue(self.guard.reload()["reloaded"])

        good = self.guard.admit({"id": "a", "source": "cms-a", "site": "shop"})
        self.assertTrue(good["admitted"])
        self.assertEqual(good["list_version"], "v1")

        wrong_site = self.guard.admit({"id": "b", "source": "cms-a", "site": "news"})
        self.assertFalse(wrong_site["admitted"])
        self.assertEqual(wrong_site["reason"], "site_not_allowed")

        wrong_source = self.guard.admit(
            {"id": "c", "source": "legacy", "site": "shop"}
        )
        self.assertFalse(wrong_source["admitted"])
        self.assertEqual(wrong_source["reason"], "source_not_allowed")

    def test_deny_list_wins_over_allow(self):
        self.write_list(
            "v1",
            sources={"allow": ["cms-*"], "deny": ["cms-bad"]},
            sites={"deny": ["*.internal"]},
        )
        self.assertTrue(self.guard.reload()["reloaded"])

        source = self.guard.admit({"id": "a", "source": "cms-bad", "site": "shop"})
        self.assertEqual(source["reason"], "source_denied")

        site = self.guard.admit({"id": "b", "source": "cms-a", "site": "db.internal"})
        self.assertEqual(site["reason"], "site_denied")

    def test_duplicate_by_fingerprint_is_rejected(self):
        self.write_list("v1", sites={"allow": ["shop"]})
        self.guard.reload()

        first = self.guard.admit(
            {"id": "a", "source": "cms", "site": "shop", "url": "/p/1"}
        )
        self.assertTrue(first["admitted"])

        # 换了 id，但来源/站点/URL 相同 —— 指纹相同，判为重复。
        second = self.guard.admit(
            {"id": "a-copy", "source": "cms", "site": "shop", "url": "/p/1"}
        )
        self.assertFalse(second["admitted"])
        self.assertEqual(second["reason"], "duplicate")
        self.assertEqual(second["fingerprint"], first["fingerprint"])
        self.assertEqual(second["audit"][0]["duplicate_of"], "a")

        explicit = self.guard.admit({"id": "x", "fingerprint": "fp-1", "site": "shop"})
        self.assertTrue(explicit["admitted"])
        dup = self.guard.admit({"id": "y", "fingerprint": "fp-1", "site": "shop"})
        self.assertEqual(dup["reason"], "duplicate")


class HotReloadTest(ListFileMixin, unittest.TestCase):
    def test_reload_takes_effect_without_restart(self):
        self.write_list("v1", sites={"allow": ["shop"]})
        self.assertTrue(self.guard.reload()["reloaded"])
        self.assertTrue(self.guard.admit({"id": "a", "site": "shop"})["admitted"])

        self.write_list("v2", sites={"allow": ["news"]})
        result = self.guard.reload()
        self.assertTrue(result["reloaded"])
        self.assertEqual(result["version"], "v2")
        self.assertEqual(result["previous_version"], "v1")

        self.assertFalse(self.guard.admit({"id": "b", "site": "shop"})["admitted"])
        self.assertTrue(self.guard.admit({"id": "c", "site": "news"})["admitted"])

    def test_broken_list_keeps_old_snapshot(self):
        self.write_list("v1", sites={"allow": ["shop"]})
        self.guard.reload()

        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        result = self.guard.reload()
        self.assertFalse(result["reloaded"])
        self.assertTrue(result["kept"])
        self.assertEqual(result["reason"], "invalid_list")
        self.assertEqual(result["version"], "v1")
        self.assertTrue(self.guard.admit({"id": "a", "site": "shop"})["admitted"])

    def test_list_without_version_is_rejected(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"sites": {}}, handle)
        result = self.guard.reload()
        self.assertFalse(result["reloaded"])
        self.assertEqual(result["reason"], "invalid_list")

    def test_structurally_invalid_list_keeps_old_snapshot(self):
        self.write_list("v1", sites={"allow": ["shop"]})
        self.guard.reload()

        self.write_list("v2", sites={"allow": ["shop", 42]})
        result = self.guard.reload()
        self.assertFalse(result["reloaded"])
        self.assertTrue(result["kept"])
        self.assertEqual(result["version"], "v1")
        self.assertTrue(self.guard.admit({"id": "a", "site": "shop"})["admitted"])

    def test_same_version_with_new_content_is_rejected(self):
        self.write_list("v1", sites={"allow": ["shop"]})
        self.guard.reload()

        self.write_list("v1", sites={"allow": ["news"]})
        result = self.guard.reload()
        self.assertFalse(result["reloaded"])
        self.assertEqual(result["reason"], "version_conflict")
        self.assertTrue(self.guard.admit({"id": "a", "site": "shop"})["admitted"])

    def test_future_list_waits_until_effective(self):
        future = self.clock.moment + timedelta(minutes=10)
        self.write_list("v1", sites={"allow": ["shop"]})
        self.guard.reload()
        self.write_list("v2", sites={"allow": ["news"]}, effective_at=future.isoformat())

        pending = self.guard.reload()
        self.assertFalse(pending["reloaded"])
        self.assertEqual(pending["reason"], "not_effective_yet")
        self.assertEqual(pending["version"], "v1")
        self.assertEqual(pending["pending_version"], "v2")

        self.clock.moment = future + timedelta(seconds=1)
        active = self.guard.reload()
        self.assertTrue(active["reloaded"])
        self.assertEqual(active["version"], "v2")
        self.assertTrue(self.guard.admit({"id": "a", "site": "news"})["admitted"])


class AuditTest(ListFileMixin, unittest.TestCase):
    def test_rejection_writes_audit_with_reason_and_version(self):
        self.write_list("v7", sites={"deny": ["blocked"]})
        self.guard.reload()

        result = self.guard.admit({"id": "bad-1", "site": "blocked"})
        self.assertFalse(result["admitted"])
        self.assertEqual(len(result["audit"]), 1)

        entry = result["audit"][0]
        self.assertEqual(entry["event"], "reject")
        self.assertEqual(entry["task"], "bad-1")
        self.assertEqual(entry["reason"], "site_denied")
        self.assertTrue(entry["reason_text"])
        self.assertEqual(entry["list_version"], "v7")
        self.assertIn("at", entry)
        self.assertEqual(self.guard.audit_log(), result["audit"])

    def test_admission_is_not_audited(self):
        self.write_list("v1")
        self.guard.reload()
        result = self.guard.admit({"id": "ok", "site": "shop"})
        self.assertTrue(result["admitted"])
        self.assertEqual(self.guard.audit_log(), [])

    def test_rejections_export_and_site_summary(self):
        self.write_list("v3", sites={"deny": ["blocked"], "allow": ["shop"]})
        self.guard.reload()
        self.guard.admit({"id": "1", "site": "shop"})
        self.guard.admit({"id": "2", "site": "blocked"})
        self.guard.admit({"id": "3", "site": "blocked"})

        summary = self.guard.summary()
        self.assertEqual(summary["list_version"], "v3")
        self.assertEqual(summary["sites"]["shop"]["admitted"], 1)
        self.assertEqual(summary["sites"]["blocked"]["rejected"], 2)
        self.assertEqual(summary["sites"]["blocked"]["reasons"]["site_denied"], 2)
        self.assertEqual(summary["totals"], {"admitted": 1, "rejected": 2, "tasks": 3})

        export_path = os.path.join(self._tmp.name, "rejections.json")
        exported = self.guard.export_rejections(export_path)
        self.assertEqual(exported["count"], 2)
        self.assertEqual(exported["list_version"], "v3")
        with open(export_path, encoding="utf-8") as handle:
            on_disk = json.load(handle)
        self.assertEqual([item["task"] for item in on_disk["rejections"]], ["2", "3"])

    def test_reload_history_records_failures(self):
        self.write_list("v1")
        self.guard.reload()
        self.guard.reload()
        history = self.guard.reload_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[1]["reason"], "unchanged")


if __name__ == "__main__":
    unittest.main()
