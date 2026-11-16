import unittest

from hostbaseline import check, report, reset


class BaselineTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_full_match_passes(self):
        got = check({
            "host": "lnx-01",
            "os": "linux",
            "items": {"firewall": "on", "antivirus": "on", "ssh_root_login": "no"},
        })
        self.assertEqual(got["failed"], [])

    def test_windows_lacks_ssh_item(self):
        got = check({
            "host": "win-01",
            "os": "windows",
            "items": {"firewall": "on", "antivirus": "on"},
        })
        self.assertIn("ssh_root_login", got["failed"])

    def test_no_not_applicable_bucket(self):
        got = check({"host": "win-02", "os": "windows", "items": {}})
        self.assertEqual(got["not_applicable"], [])

    def test_report_counts_all_checks(self):
        check({"host": "a", "items": {"firewall": "on"}})
        got = report()
        self.assertEqual(got["checks"], 3)
        self.assertEqual(got["rate"], 0.333)

    def test_host_required(self):
        with self.assertRaises(ValueError):
            check({"os": "linux"})
