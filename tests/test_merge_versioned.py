import subprocess
import sys
import unittest
from pathlib import Path

from syncconflict import ConflictError, MergeResult, SyncError, merge

REPO_ROOT = Path(__file__).resolve().parent.parent


def row(price, version, updated_at=1000, source="shop-A", **extra):
    base = {
        "id": 1,
        "price": price,
        "version": version,
        "updated_at": updated_at,
        "source": source,
    }
    base.update(extra)
    return base


class VersionCompareTest(unittest.TestCase):
    def test_higher_remote_version_wins(self):
        r = merge(row("1", 2), row("2", 3))
        self.assertEqual(r["price"], "2")
        self.assertEqual(r.winner, "remote")
        self.assertEqual(r.conflicts, [])

    def test_higher_local_version_wins(self):
        r = merge(row("1", 5), row("2", 3))
        self.assertEqual(r["price"], "1")
        self.assertEqual(r.winner, "local")

    def test_equal_version_same_content_no_conflict(self):
        r = merge(row("1", 3, updated_at=1000), row("1", 3, updated_at=1001, source="shop-B"))
        self.assertEqual(r.conflicts, [])
        self.assertEqual(r["updated_at"], 1001)
        self.assertEqual(r.winner, "remote")

    def test_result_is_dict_with_metadata(self):
        r = merge(row("1", 3), row("2", 3, source="shop-B"))
        self.assertIsInstance(r, MergeResult)
        self.assertEqual(r["version"], 3)
        self.assertEqual(r["source"], "shop-B")


class StrategyTest(unittest.TestCase):
    def setUp(self):
        self.local = row("10", 3)
        self.remote = row("12", 3, updated_at=1001, source="shop-B")

    def test_local_first(self):
        r = merge(self.local, self.remote, strategy="local")
        self.assertEqual(r["price"], "10")
        self.assertEqual(r.winner, "local")
        self.assertEqual([c.field for c in r.conflicts], ["price"])

    def test_remote_first(self):
        r = merge(self.local, self.remote, strategy="remote")
        self.assertEqual(r["price"], "12")
        self.assertEqual(r.winner, "remote")

    def test_manual_resolver_picks_winner(self):
        seen = []

        def resolver(info):
            seen.append(info)
            return "local"

        r = merge(self.local, self.remote, strategy="manual", resolver=resolver)
        self.assertEqual(r["price"], "10")
        self.assertEqual(r.winner, "local")
        self.assertEqual(seen[0]["fields"], ["price"])

    def test_manual_without_resolver_raises(self):
        with self.assertRaises(ConflictError):
            merge(self.local, self.remote, strategy="manual")

    def test_manual_without_conflict_needs_no_resolver(self):
        r = merge(row("1", 3), row("1", 3), strategy="manual")
        self.assertEqual(r.conflicts, [])

    def test_unknown_strategy_raises(self):
        with self.assertRaises(SyncError):
            merge(self.local, self.remote, strategy="newest")

    def test_resolver_bad_return_raises(self):
        with self.assertRaises(SyncError):
            merge(self.local, self.remote, strategy="manual", resolver=lambda i: "both")


class TombstoneTest(unittest.TestCase):
    def test_newer_tombstone_not_resurrected(self):
        tomb = row(None, 4, deleted=True)
        del tomb["price"]
        r = merge(tomb, row("9", 3, source="shop-B"))
        self.assertTrue(r["deleted"])
        self.assertEqual(r["version"], 4)
        self.assertNotIn("price", r)

    def test_newer_update_beats_older_tombstone(self):
        tomb = row(None, 3, deleted=True)
        del tomb["price"]
        r = merge(tomb, row("9", 4, source="shop-B"))
        self.assertNotIn("deleted", r)
        self.assertEqual(r["price"], "9")

    def test_equal_version_delete_vs_update_is_conflict(self):
        tomb = row(None, 3, deleted=True)
        del tomb["price"]
        r = merge(tomb, row("9", 3, source="shop-B"), strategy="remote")
        self.assertEqual(r["price"], "9")
        fields = [c.field for c in r.conflicts]
        self.assertIn("deleted", fields)
        self.assertIn("price", fields)


class ConflictReportTest(unittest.TestCase):
    def test_conflict_entries_carry_values_and_reason(self):
        r = merge(row("10", 3), row("12", 3, source="shop-B"), strategy="local")
        self.assertEqual(len(r.conflicts), 1)
        c = r.conflicts[0]
        self.assertEqual(c.field, "price")
        self.assertEqual(c.local, "10")
        self.assertEqual(c.remote, "12")
        self.assertEqual(c.winner, "local")
        self.assertIn("local", c.reason)

    def test_meta_fields_not_in_conflicts(self):
        r = merge(row("10", 3), row("12", 3, updated_at=2000, source="shop-B"))
        self.assertEqual([c.field for c in r.conflicts], ["price"])


class ErrorPathTest(unittest.TestCase):
    def test_one_sided_version_raises(self):
        with self.assertRaises(SyncError):
            merge({"price": "1"}, row("2", 3))
        with self.assertRaises(SyncError):
            merge(row("1", 3), {"price": "2"})

    def test_missing_meta_keys_raise(self):
        bad = {"version": 1, "updated_at": 1000}
        with self.assertRaises(SyncError):
            merge(bad, row("2", 1))

    def test_bad_version_raises(self):
        for v in (-1, 1.5, True, "3"):
            with self.assertRaises(SyncError):
                merge(row("1", v), row("2", 3))

    def test_bad_updated_at_and_source_raise(self):
        with self.assertRaises(SyncError):
            merge(row("1", 3, updated_at="now"), row("2", 3))
        with self.assertRaises(SyncError):
            merge(row("1", 3, source=""), row("2", 3))

    def test_bad_deleted_raises(self):
        with self.assertRaises(SyncError):
            merge(row("1", 3, deleted=1), row("2", 3))


class DemoOutputTest(unittest.TestCase):
    def test_demo_output_matches_resolution(self):
        out = subprocess.run(
            [sys.executable, "demo.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertIn("local-first: 10 local ['price']", out)
        self.assertIn("remote-first: 12 remote ['price']", out)
        self.assertIn("manual: 12 remote ['price']", out)
        self.assertIn("tombstone: True 4 local", out)


if __name__ == "__main__":
    unittest.main()
