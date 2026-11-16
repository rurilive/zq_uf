import unittest
from datetime import datetime, timezone

from surfacemap import changes, confirm, report, reset, stale, surface

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 20, 9, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 2, 10, 9, 0, tzinfo=timezone.utc)

ENTRY = {
    "domain": "gw.example.com",
    "address": "10.2.0.9",
    "port": 443,
    "owner": "pay",
    "source": "cmdb",
}


class MergeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_same_entry_registered_twice(self):
        surface([ENTRY], now=T0)
        result = surface([ENTRY], now=T0)
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(report(now=T0)["total"], 1)

    def test_domain_case_is_merged(self):
        surface([ENTRY], now=T0)
        surface([dict(ENTRY, domain="GW.Example.com")], now=T0)
        self.assertEqual(report(now=T0)["total"], 1)

    def test_different_port_is_separate_entry(self):
        surface([ENTRY], now=T0)
        surface([dict(ENTRY, port=8443)], now=T0)
        self.assertEqual(report(now=T0)["total"], 2)

    def test_sources_merge_and_keep_first_seen(self):
        surface([ENTRY], now=T0)
        surface([dict(ENTRY, source="scan")], now=T1)
        row = report(now=T1)["entries"][0]
        self.assertEqual(row["sources"], ["cmdb", "scan"])
        self.assertEqual(row["source_seen"]["cmdb"], T0.isoformat())
        self.assertEqual(row["source_seen"]["scan"], T1.isoformat())
        self.assertEqual(report(now=T1)["sources"], {"cmdb": 1, "scan": 1})

    def test_port_defaults_to_443(self):
        surface([{"domain": "cdn.example.com", "address": "10.2.0.11"}], now=T0)
        self.assertEqual(report(now=T0)["entries"][0]["port"], 443)

    def test_earliest_discovered_at_wins(self):
        surface([dict(ENTRY, discovered_at=T1.isoformat())], now=T1)
        surface([dict(ENTRY, source="scan", discovered_at=T0.isoformat())], now=T2)
        row = report(now=T2)["entries"][0]
        self.assertEqual(row["discovered_at"], T0.isoformat())

    def test_missing_address_raises(self):
        with self.assertRaises(ValueError):
            surface([{"domain": "cdn.example.com"}])

    def test_entries_must_be_list(self):
        with self.assertRaises(ValueError):
            surface(ENTRY)

    def test_surface_returns_status_buckets(self):
        surface([ENTRY, dict(ENTRY, domain="old.example.com", address="10.2.0.5")], now=T0)
        surface([dict(ENTRY, domain="old.example.com", address="10.2.0.5", offline=True)], now=T1)
        surface([{"domain": "api.example.com", "address": "10.2.0.10"}], now=T1)
        result = surface([], now=T1)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["active"], 2)
        self.assertEqual(result["offline"], 1)
        self.assertEqual(result["pending"], 1)
        self.assertEqual([row["domain"] for row in result["offline_entries"]],
                         ["old.example.com"])
        self.assertEqual([row["domain"] for row in result["pending_entries"]],
                         ["api.example.com"])


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_offline_entry_marked_and_kept(self):
        surface([ENTRY], now=T0)
        result = surface([dict(ENTRY, offline=True)], now=T1)
        self.assertEqual(result["active"], 0)
        self.assertEqual(result["offline"], 1)
        rep = report(now=T1)
        self.assertEqual(rep["total"], 1)
        self.assertEqual(rep["active_entries"], [])
        row = rep["offline_entries"][0]
        self.assertEqual(row["status"], "offline")
        self.assertEqual(row["offline_at"], T1.isoformat())

    def test_offline_twice_keeps_first_offline_time(self):
        surface([ENTRY], now=T0)
        surface([dict(ENTRY, offline=True)], now=T1)
        surface([dict(ENTRY, offline=True)], now=T2)
        row = report(now=T2)["offline_entries"][0]
        self.assertEqual(row["offline_at"], T1.isoformat())

    def test_restore_active_again_and_keep_last_offline(self):
        surface([ENTRY], now=T0)
        surface([dict(ENTRY, offline=True)], now=T1)
        surface([ENTRY], now=T2)
        rep = report(now=T2)
        self.assertEqual(rep["active"], 1)
        row = rep["active_entries"][0]
        self.assertEqual(row["status"], "active")
        self.assertIsNone(row["offline_at"])
        self.assertEqual(row["restored_at"], T2.isoformat())
        self.assertEqual(row["last_offline_at"], T1.isoformat())

    def test_status_offline_key_works(self):
        surface([dict(ENTRY, status="offline")], now=T0)
        self.assertEqual(report(now=T0)["offline"], 1)

    def test_confirm_refreshes_and_stale_lists(self):
        surface([ENTRY], now=T0)
        surface([dict(ENTRY, domain="api.example.com", address="10.2.0.10")], now=T0)
        confirm([ENTRY], now=T1)
        stale_rows = stale(days=10, now=T1)
        self.assertEqual(len(stale_rows), 1)
        self.assertEqual(stale_rows[0]["domain"], "api.example.com")
        self.assertEqual(report(now=T1, stale_days=10)["stale"], 1)

    def test_confirm_all(self):
        surface([ENTRY], now=T0)
        result = confirm(now=T1)
        self.assertEqual(result["confirmed"], 1)
        self.assertEqual(stale(days=1, now=T1), [])

    def test_confirm_unknown_entry_reported_missing(self):
        result = confirm([ENTRY], now=T0)
        self.assertEqual(result["confirmed"], 0)
        self.assertEqual(result["missing"][0]["domain"], "gw.example.com")


class OwnershipTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_unknown_owner_stays_pending_and_not_in_by_owner(self):
        surface([dict(ENTRY, owner=None)], now=T0)
        rep = report(now=T0)
        self.assertEqual(rep["pending"], 1)
        self.assertEqual(rep["by_owner"], {})
        self.assertEqual(rep["pending_entries"][0]["pending_reason"], "owner_unknown")

    def test_owner_filled_later_leaves_pending(self):
        surface([dict(ENTRY, owner=None)], now=T0)
        surface([ENTRY], now=T1)
        rep = report(now=T1)
        self.assertEqual(rep["pending"], 0)
        self.assertEqual(rep["by_owner"], {"pay": 1})

    def test_owner_conflict_is_pending(self):
        surface([ENTRY], now=T0)
        result = surface([dict(ENTRY, owner="order")], now=T1)
        rep = report(now=T1)
        self.assertEqual(result["pending"], 1)
        self.assertEqual(rep["pending_entries"][0]["owner_candidates"], ["order"])
        self.assertEqual(rep["pending_entries"][0]["pending_reason"], "owner_conflict")

    def test_groups_and_owner_filter(self):
        surface([
            ENTRY,
            dict(ENTRY, domain="api.example.com", address="10.2.0.10"),
            dict(ENTRY, domain="cdn.example.com", address="10.2.0.11", owner="web"),
        ], now=T0)
        rep = report(now=T0)
        self.assertEqual(rep["by_owner"], {"pay": 2, "web": 1})
        self.assertEqual(len(rep["groups"]["pay"]), 1 + 1)
        filtered = report(owner="web", now=T0)
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["by_owner"], {"web": 1})

    def test_status_filter(self):
        surface([ENTRY, dict(ENTRY, domain="old.example.com", address="10.2.0.5")], now=T0)
        surface([dict(ENTRY, domain="old.example.com", address="10.2.0.5", offline=True)], now=T1)
        rep = report(status="offline", now=T1)
        self.assertEqual(rep["total"], 1)
        self.assertEqual(rep["offline_entries"][0]["domain"], "old.example.com")

    def test_third_party_flagged(self):
        surface([dict(ENTRY, third_party=True)], now=T0)
        rep = report(now=T0)
        self.assertEqual(rep["third_party"], 1)
        self.assertTrue(rep["third_party_entries"][0]["third_party"])


class ChangesTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_changes_by_month(self):
        surface([ENTRY], now=T0)
        surface([dict(ENTRY, domain="old.example.com", address="10.2.0.5")], now=T0)
        surface([dict(ENTRY, domain="old.example.com", address="10.2.0.5", offline=True)], now=T1)
        surface([dict(ENTRY, domain="old.example.com", address="10.2.0.5")], now=T2)

        january = changes("2026-01")
        self.assertEqual(january["counts"]["added"], 2)
        self.assertEqual(january["counts"]["offline"], 1)
        self.assertEqual(january["counts"]["restored"], 0)
        february = changes("2026-02")
        self.assertEqual(february["counts"]["restored"], 1)

        everything = changes()
        self.assertEqual(sorted(everything), ["2026-01", "2026-02"])

    def test_changes_empty_month(self):
        bucket = changes("2026-03")
        self.assertEqual(bucket["month"], "2026-03")
        self.assertEqual(bucket["counts"]["added"], 0)

    def test_report_includes_this_month_changes(self):
        surface([ENTRY], now=T0)
        rep = report(now=T0)
        self.assertEqual(rep["changes"]["month"], "2026-01")
        self.assertEqual(rep["changes"]["counts"]["added"], 1)

    def test_changes_owner_filter(self):
        surface([ENTRY, dict(ENTRY, domain="w.example.com", address="10.2.0.20", owner="web")], now=T0)
        bucket = changes("2026-01", owner="web")
        self.assertEqual(bucket["counts"]["added"], 1)
        self.assertEqual(bucket["added"][0]["owner"], "web")


if __name__ == "__main__":
    unittest.main()
