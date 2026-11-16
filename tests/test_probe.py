import unittest

from dirprobe import load, probe, report

WORDS = ["admin", "backup.zip", "login", "static"]
RESPONSES = {
    "admin": {"status": 200},
    "backup.zip": {"status": 200},
    "login": {"status": 403},
    "static": {"status": 429, "body": "too many requests"},
}


class ProbeTest(unittest.TestCase):
    def test_blocked_page_counted_as_hit(self):
        load("shop", WORDS, RESPONSES)
        out = probe("shop")
        self.assertEqual(len(out["hits"]), 4)
        self.assertIn("static", [hit["path"] for hit in out["hits"]])

    def test_concurrency_stays_fixed(self):
        load("shop", WORDS, RESPONSES)
        out = probe("shop")
        self.assertEqual(out["concurrency"], 8)
        self.assertEqual(out["blocked"], 1)

    def test_no_resume_position(self):
        load("shop", WORDS, RESPONSES)
        out = probe("shop")
        self.assertEqual(out["resume"], 0)
        self.assertFalse(out["stopped"])

    def test_report_has_no_groups(self):
        load("shop", WORDS, RESPONSES)
        probe("shop")
        rep = report("shop")
        self.assertEqual(rep["hits"], 4)
        self.assertEqual(rep["groups"], {})

    def test_reject_empty_wordlist(self):
        with self.assertRaises(ValueError):
            load("shop", [], {})


if __name__ == "__main__":
    unittest.main()
