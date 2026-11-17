import os
import tempfile
import unittest

from surfacemap import (
    changes,
    export_changes,
    monthly_changes,
    report,
    reset,
    surface,
)

ENTRY = {
    "domain": "gw.example.com",
    "address": "10.2.0.9",
    "port": 443,
    "owner": "pay",
    "source": "cmdb",
}


class EntriesTest(unittest.TestCase):
    def setUp(self):
        reset()

    # ---- 合并 ----------------------------------------------------------

    def test_same_entry_registered_twice_merges(self):
        surface([ENTRY])
        result = surface([dict(ENTRY, source="scan")])
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["merged"], 1)
        self.assertEqual(result["total"], 1)
        row = report()["entries"][0]
        self.assertEqual(row["sources"], ["cmdb", "scan"])

    def test_different_port_is_a_different_entry(self):
        surface([ENTRY, dict(ENTRY, port=8443)])
        self.assertEqual(report()["total"], 2)

    def test_domain_match_is_case_insensitive(self):
        surface([ENTRY, dict(ENTRY, domain="GW.Example.com")])
        self.assertEqual(report()["total"], 1)

    def test_owner_counted_per_merged_entry(self):
        surface([ENTRY, dict(ENTRY, domain="api.example.com", address="10.2.0.10")])
        self.assertEqual(report()["by_owner"], {"pay": 2})

    def test_port_defaults_to_443(self):
        surface([{"domain": "cdn.example.com", "address": "10.2.0.11"}])
        self.assertEqual(report()["entries"][0]["port"], 443)

    def test_missing_address_raises(self):
        with self.assertRaises(ValueError):
            surface([{"domain": "cdn.example.com"}])

    # ---- 下线 / 恢复 ---------------------------------------------------

    def test_offline_entry_marked_offline_and_kept(self):
        surface([ENTRY])
        surface([dict(ENTRY, offline=True, seen_at="2026-03-01")])
        data = report()
        self.assertEqual(data["counts"]["offline"], 1)
        self.assertEqual(data["counts"]["active"], 0)
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(data["offline"][0]["offline_at"], "2026-03-01")
        self.assertNotIn(ENTRY["domain"], [r["domain"] for r in data["active"]])

    def test_offline_entry_does_not_count_into_owner(self):
        surface([ENTRY, dict(ENTRY, offline=True)])
        self.assertEqual(report()["by_owner"], {})

    def test_restore_clears_offline_at(self):
        surface([ENTRY])
        surface([dict(ENTRY, offline=True, seen_at="2026-03-01")])
        result = surface([dict(ENTRY, seen_at="2026-04-01")])
        self.assertEqual(result["restored"], 1)
        row = report()["entries"][0]
        self.assertEqual(row["status"], "active")
        self.assertIsNone(row["offline_at"])

    def test_offline_timestamp_not_moved_by_later_offline_scan(self):
        surface([ENTRY])
        surface([dict(ENTRY, offline=True, seen_at="2026-03-01")])
        surface([dict(ENTRY, offline=True, seen_at="2026-05-01")])
        self.assertEqual(report()["offline"][0]["offline_at"], "2026-03-01")

    # ---- 归属 ----------------------------------------------------------

    def test_missing_owner_goes_pending(self):
        surface([{"domain": "x.example.com", "address": "10.0.0.1"}])
        data = report()
        self.assertEqual(data["counts"]["pending"], 1)
        self.assertEqual(data["pending"][0]["owner"], "unknown")
        self.assertEqual(data["counts"]["active"], 0)

    def test_conflicting_owners_go_pending(self):
        surface([ENTRY, dict(ENTRY, owner="order")])
        data = report()
        self.assertEqual(data["counts"]["pending"], 1)
        self.assertEqual(data["entries"][0]["owners"], ["pay", "order"])
        self.assertEqual(data["pending"][0]["domain"], ENTRY["domain"])

    def test_same_owner_registered_again_stays_active(self):
        surface([ENTRY, dict(ENTRY)])
        self.assertEqual(report()["counts"]["active"], 1)

    def test_third_party_is_flagged(self):
        surface([dict(ENTRY, third_party=True)])
        self.assertEqual(len(report()["third_party"]), 1)
        self.assertTrue(report()["third_party"][0]["third_party"])

    # ---- 时间 / 来源 ---------------------------------------------------

    def test_first_seen_kept_at_earliest_and_confirmed_at_latest(self):
        surface([dict(ENTRY, seen_at="2026-02-01")])
        surface([dict(ENTRY, seen_at="2026-06-01")])
        row = report()["entries"][0]
        self.assertEqual(row["first_seen"], "2026-02-01")
        self.assertEqual(row["last_seen"], "2026-06-01")
        self.assertEqual(row["confirmed_at"], "2026-06-01")

    def test_explicit_first_seen_is_recorded(self):
        surface([dict(ENTRY, first_seen="2026-01-05", seen_at="2026-06-01")])
        self.assertEqual(report()["entries"][0]["first_seen"], "2026-01-05")

    def test_sources_distribution_counts_each_source(self):
        surface([
            ENTRY,
            dict(ENTRY, domain="api.example.com", address="10.2.0.10", source="scan"),
        ])
        self.assertEqual(report()["sources"], {"cmdb": 1, "scan": 1})

    def test_stale_entry_listed_separately(self):
        surface([dict(ENTRY, seen_at="2026-01-01")])
        data = report(as_of="2026-06-01", stale_days=90)
        self.assertEqual(len(data["stale"]), 1)
        self.assertEqual(data["stale"][0]["domain"], ENTRY["domain"])

    def test_recently_confirmed_not_stale(self):
        surface([dict(ENTRY, seen_at="2026-05-20")])
        self.assertEqual(report(as_of="2026-06-01", stale_days=90)["stale"], [])

    def test_offline_entry_not_in_stale(self):
        surface([dict(ENTRY, seen_at="2026-01-01", offline=True)])
        self.assertEqual(report(as_of="2026-06-01")["stale"], [])

    # ---- 变更日志 ------------------------------------------------------

    def test_changes_record_added_and_offline(self):
        surface([dict(ENTRY, seen_at="2026-03-02")])
        surface([dict(ENTRY, offline=True, seen_at="2026-03-09")])
        actions = [item["action"] for item in changes("2026-03")]
        self.assertEqual(actions, ["added", "offline"])

    def test_changes_filtered_by_month(self):
        surface([dict(ENTRY, seen_at="2026-03-02")])
        surface([dict(ENTRY, seen_at="2026-04-02", owner="order")])
        self.assertEqual(len(changes("2026-03")), 1)
        self.assertEqual(len(changes("2026-04")), 1)

    def test_monthly_changes_summary(self):
        surface([dict(ENTRY, seen_at="2026-03-02")])
        summary = monthly_changes()
        self.assertEqual(summary["2026-03"]["added"], 1)

    def test_export_changes_to_file(self):
        surface([dict(ENTRY, seen_at="2026-03-02")])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "changes.json")
            data = export_changes(path, month="2026-03")
            self.assertTrue(os.path.exists(path))
            self.assertEqual(len(data), 1)

    # ---- 报告筛选 ------------------------------------------------------

    def test_report_filtered_by_owner(self):
        surface([
            ENTRY,
            dict(ENTRY, domain="api.example.com", address="10.2.0.10", owner="order"),
        ])
        data = report(owner="pay")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["entries"][0]["domain"], ENTRY["domain"])
        self.assertEqual(data["by_owner"], {"pay": 1})

    def test_report_changes_respect_owner_filter(self):
        surface([ENTRY])
        surface([dict(ENTRY, domain="api.example.com", address="10.2.0.10",
                      owner="order")])
        data = report(owner="pay")
        self.assertTrue(all(item["owner"] == "pay" for item in data["changes"]))

    def test_bad_date_rejected(self):
        with self.assertRaises(ValueError):
            surface([dict(ENTRY, seen_at="not-a-date")])


if __name__ == "__main__":
    unittest.main()
