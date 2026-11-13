import json
import os
import tempfile
import unittest

from sitemaptool import refresh_all, refresh_site


def entry(loc, lastmod, changefreq="daily"):
    return {"loc": loc, "lastmod": lastmod, "changefreq": changefreq}


class IncrementalStoreTest(unittest.TestCase):
    def test_refresh_writes_only_when_entries_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            first = refresh_all(
                [entry("http://a.example/p1", "2026-05-01")], out, now=0
            )
            path = first["reports"]["a.example"]["path"]
            self.assertTrue(first["reports"]["a.example"]["written"])
            first_mtime = os.stat(path).st_mtime_ns
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["entries"][0]["status"], "new")

            unchanged = refresh_site(out, "a.example", [
                entry("http://a.example/p1", "2026-05-01")
            ])
            self.assertFalse(unchanged["written"])
            self.assertEqual(os.stat(path).st_mtime_ns, first_mtime)

            second = refresh_all([
                entry("http://a.example/p1", "2026-05-02"),
                entry("http://a.example/p2", "2026-05-03", "weekly"),
            ], out, now=0)
            self.assertTrue(second["reports"]["a.example"]["written"])
            with open(path, encoding="utf-8") as handle:
                refreshed = json.load(handle)
            by_loc = {item["loc"]: item for item in refreshed["entries"]}
            self.assertEqual(by_loc["http://a.example/p1"]["status"], "updated")
            self.assertEqual(by_loc["http://a.example/p2"]["status"], "new")

    def test_removed_entries_become_stale_and_exclusions_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            rules = {"a.example": "User-agent: *\nDisallow: /blocked\n"}
            refresh_all([
                entry("http://a.example/keep", "2026-05-01"),
                entry("http://a.example/gone", "2026-05-01"),
                entry("http://a.example/blocked", "2026-05-01"),
            ], out, robots_rules=rules, now=0)

            unchanged = refresh_all([
                entry("http://a.example/keep", "2026-05-01"),
                entry("http://a.example/gone", "2026-05-01"),
                entry("http://a.example/blocked", "2026-05-01"),
            ], out, robots_rules=rules, now=0)
            self.assertFalse(unchanged["reports"]["a.example"]["written"])

            result = refresh_all([
                entry("http://a.example/keep", "2026-05-01"),
                entry("http://a.example/blocked", "2026-05-01"),
            ], out, robots_rules=rules, now=0)
            self.assertTrue(result["reports"]["a.example"]["written"])

            refresh_all([
                entry("http://a.example/keep", "2026-05-01"),
                entry("http://a.example/blocked", "2026-05-01"),
            ], out, robots_rules=rules, now=0)
            with open(result["reports"]["a.example"]["path"],
                      encoding="utf-8") as handle:
                payload = json.load(handle)
            statuses = {item["loc"]: item["status"] for item in payload["entries"]}
            self.assertEqual(statuses["http://a.example/keep"], "active")
            self.assertEqual(statuses["http://a.example/gone"], "stale")
            self.assertNotIn("http://a.example/blocked", statuses)
            self.assertEqual(
                payload["excluded"][0]["loc"], "http://a.example/blocked"
            )


if __name__ == "__main__":
    unittest.main()
