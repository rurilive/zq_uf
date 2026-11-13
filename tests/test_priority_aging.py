import calendar
import unittest
from datetime import datetime, timezone

from sitemaptool import build_sites, entry_score

NOW = calendar.timegm(datetime(2026, 6, 1, tzinfo=timezone.utc).utctimetuple())


def entry(loc, lastmod="", changefreq=""):
    return {"loc": loc, "lastmod": lastmod, "changefreq": changefreq}


class PriorityAgingTest(unittest.TestCase):
    def test_duplicates_are_grouped_and_ranked_per_site(self):
        entries = [
            entry("http://a.example/fresh", "2026-05-30", "daily"),
            entry("http://a.example/fresh", "2026-05-30", "daily"),
            entry("http://b.example/other", "2026-05-29", "weekly"),
            entry("http://a.example/stale", "2025-01-01", "yearly"),
        ]
        sites = build_sites(entries, now=NOW)

        self.assertEqual(set(sites), {"a.example", "b.example"})
        a_locs = [item["loc"] for item in sites["a.example"]]
        self.assertEqual(
            a_locs,
            ["http://a.example/fresh", "http://a.example/stale"],
        )
        self.assertEqual(
            [item["priority"] for item in sites["a.example"]], [1, 2]
        )

    def test_long_idle_entries_decay_step_by_step(self):
        fresh = entry("http://a.example/fresh", "2026-05-31", "monthly")
        idle = entry("http://a.example/idle", "2026-05-31", "monthly")
        after_quarter = NOW + 180 * 86400
        after_year = NOW + 365 * 86400

        fresh_score = entry_score(fresh, now=NOW)
        quarter_score = entry_score(idle, now=after_quarter)
        year_score = entry_score(idle, now=after_year)

        self.assertLess(quarter_score, fresh_score)
        self.assertLess(year_score, quarter_score)
        self.assertAlmostEqual(
            quarter_score, entry_score(fresh, now=after_quarter), places=6
        )

    def test_frequency_breaks_ties_for_same_age(self):
        sites = build_sites(
            [
                entry("http://a.example/daily", "2026-05-31", "daily"),
                entry("http://a.example/yearly", "2026-05-31", "yearly"),
            ],
            now=NOW,
        )
        self.assertEqual(
            [item["loc"] for item in sites["a.example"]],
            ["http://a.example/daily", "http://a.example/yearly"],
        )


if __name__ == "__main__":
    unittest.main()
