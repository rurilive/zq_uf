import unittest

from hostbaseline import check, export_remediation, report, reset


LINUX_FULL = {
    "firewall": "on",
    "ssh_root_login": "no",
    "selinux": "enforcing",
}

WINDOWS_FULL = {
    "firewall": "on",
    "antivirus": "on",
    "smb1": "off",
}


def verdicts(run):
    return {result["item"]: result["verdict"] for result in run["results"]}


class BaselineTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_linux_full_match_passes(self):
        got = check({"host": "lnx-01", "os": "linux", "items": LINUX_FULL})
        self.assertEqual(got["failed"], [])
        self.assertEqual(got["not_applicable"], ["antivirus", "smb1"])

    def test_windows_items_are_not_applicable_on_linux(self):
        got = check({"host": "lnx-02", "os": "linux", "items": LINUX_FULL})
        states = {result["item"]: result for result in got["results"]}
        self.assertEqual(states["antivirus"]["verdict"], "not_applicable")
        self.assertEqual(states["antivirus"]["reason"], "os_mismatch")
        self.assertNotIn("antivirus", got["passed"])
        self.assertNotIn("antivirus", got["failed"])

    def test_windows_does_not_expect_linux_items(self):
        got = check({"host": "win-01", "os": "windows", "version": "10.0", "items": WINDOWS_FULL})
        self.assertEqual(got["failed"], [])
        self.assertIn("ssh_root_login", got["not_applicable"])

    def test_records_expected_and_actual(self):
        got = check({"host": "a", "os": "linux", "items": {"firewall": "off"}})
        states = {result["item"]: result for result in got["results"]}
        firewall = states["firewall"]
        self.assertEqual(firewall["expected"], "on")
        self.assertEqual(firewall["actual"], "off")
        self.assertEqual(firewall["verdict"], "fail")
        self.assertEqual(firewall["systems"], ["linux", "windows"])
        self.assertEqual(states["ssh_root_login"]["actual"], None)

    def test_version_gated_item(self):
        old = check({"host": "win-old", "os": "windows", "version": "6.1", "items": WINDOWS_FULL})
        self.assertIn("smb1", old["not_applicable"])
        self.assertEqual(
            {r["item"]: r["reason"] for r in old["results"]}["smb1"],
            "version_out_of_range",
        )

        new = check({"host": "win-new", "os": "windows", "version": "10.0", "items": WINDOWS_FULL})
        self.assertEqual(new["not_applicable"], ["ssh_root_login", "selinux"])
        self.assertIn("smb1", new["passed"])

    def test_unknown_version_still_checked(self):
        got = check({"host": "win-x", "os": "windows", "items": WINDOWS_FULL})
        self.assertIn("smb1", got["passed"])

    def test_not_applicable_excluded_from_rate(self):
        check({"host": "win-01", "os": "windows", "version": "10.0", "items": WINDOWS_FULL})
        got = report()
        # Windows 3 applicable items pass; linux-only items don't count either way.
        self.assertEqual(got["items"], 5)
        self.assertEqual(got["checks"], 3)
        self.assertEqual(got["not_applicable"], 2)
        self.assertEqual(got["rate"], 1.0)

    def test_rate_is_per_os_and_item(self):
        check({"host": "win-01", "os": "windows", "version": "10.0", "items": WINDOWS_FULL})
        check({"host": "lnx-01", "os": "linux", "items": {**LINUX_FULL, "firewall": "off"}})
        got = report()
        self.assertEqual(got["by_os"]["windows"]["rate"], 1.0)
        self.assertEqual(got["by_os"]["linux"]["rate"], 0.667)
        self.assertEqual(got["by_os"]["linux"]["failed"], 1)

        windows_rows = {row["item"]: row for row in got["by_item"]["windows"]}
        self.assertEqual(windows_rows["firewall"]["passed"], 1)
        linux_rows = {row["item"]: row for row in got["by_item"]["linux"]}
        self.assertEqual(linux_rows["firewall"]["failed"], 1)

    def test_remediation_lists_failed_items_only(self):
        check({"host": "lnx-01", "os": "linux", "items": {**LINUX_FULL, "firewall": "off"}})
        got = report()
        items = [row["item"] for row in got["remediation"]]
        self.assertEqual(items, ["firewall"])
        row = got["remediation"][0]
        self.assertEqual(row["host"], "lnx-01")
        self.assertEqual(row["expected"], "on")
        self.assertEqual(row["actual"], "off")

    def test_recheck_only_runs_failed_items(self):
        check({"host": "lnx-01", "os": "linux", "items": {**LINUX_FULL, "firewall": "off"}})
        got = check(
            {"host": "lnx-01", "os": "linux", "items": {**LINUX_FULL, "firewall": "on"}},
            recheck=True,
        )
        self.assertEqual(got["scope"], ["firewall"])
        self.assertEqual([row["item"] for row in got["results"]], ["firewall"])
        comparison = got["recheck"][0]
        self.assertEqual(comparison["first"]["actual"], "off")
        self.assertEqual(comparison["now"]["actual"], "on")
        self.assertEqual(comparison["status"], "fixed")
        self.assertEqual(got["failed"], [])

        summary = report(host="lnx-01")
        self.assertEqual(summary["recheck"][0]["status"], "fixed")
        self.assertEqual(summary["remediation"], [])

    def test_recheck_keeps_still_failing(self):
        check({"host": "a", "os": "linux", "items": {**LINUX_FULL, "firewall": "off"}})
        got = check(
            {"host": "a", "os": "linux", "items": {**LINUX_FULL, "firewall": "off"}},
            recheck=True,
        )
        self.assertEqual(got["recheck"][0]["status"], "still_failing")
        self.assertEqual(got["recheck"][0]["first"]["verdict"], "fail")

    def test_recheck_without_failures_is_empty(self):
        check({"host": "a", "os": "linux", "items": LINUX_FULL})
        got = check({"host": "a", "os": "linux", "items": LINUX_FULL}, recheck=True)
        self.assertEqual(got["results"], [])
        self.assertEqual(got["recheck"], [])

    def test_recheck_comparison_survives_later_full_check(self):
        check({"host": "a", "os": "linux", "items": {**LINUX_FULL, "firewall": "off"}})
        check({"host": "a", "os": "linux", "items": LINUX_FULL}, recheck=True)
        check({"host": "a", "os": "linux", "items": LINUX_FULL})
        summary = report(host="a")
        self.assertEqual(summary["recheck"][0]["status"], "fixed")
        self.assertEqual(summary["recheck"][0]["first"]["actual"], "off")
        self.assertEqual([run["mode"] for run in summary["history"]], ["check", "recheck", "check"])

    def test_recheck_still_failing_keeps_first_failure_time(self):
        check({"host": "a", "os": "linux", "items": {**LINUX_FULL, "firewall": "off"}})
        first = report(host="a")["results"]
        first_failed_at = {r["item"]: r.get("first_failed_at") for r in first}["firewall"]
        check({"host": "a", "os": "linux", "items": {**LINUX_FULL, "firewall": "off"}}, recheck=True)
        since = report()["remediation"][0]["since"]
        self.assertEqual(since, first_failed_at)

    def test_duplicate_host_updates_same_record(self):
        check({"host": "a", "os": "linux", "items": {**LINUX_FULL, "firewall": "off"}})
        check({"host": "a", "os": "linux", "items": LINUX_FULL})
        got = report()
        self.assertEqual(got["hosts"], 1)
        self.assertEqual(got["failed"], 0)

    def test_export_remediation_csv(self):
        import csv
        import os
        import tempfile

        check({"host": "lnx-01", "os": "linux", "items": {**LINUX_FULL, "firewall": "off"}})
        check({"host": "win-01", "os": "windows", "version": "10.0", "items": WINDOWS_FULL})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "remediation.csv")
            rows = export_remediation(path)
            self.assertEqual([row["item"] for row in rows], ["firewall"])
            with open(path, encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                written = list(reader)
            self.assertEqual(len(written), 1)
            self.assertEqual(written[0]["host"], "lnx-01")
            self.assertEqual(written[0]["expected"], "on")

    def test_previous_result_is_traceable(self):
        check({"host": "a", "os": "linux", "items": {**LINUX_FULL, "firewall": "off"}})
        got = check({"host": "a", "os": "linux", "items": {**LINUX_FULL, "firewall": "on"}})
        states = {result["item"]: result for result in got["results"]}
        self.assertEqual(states["firewall"]["previous"]["actual"], "off")
        self.assertEqual(states["firewall"]["previous"]["verdict"], "fail")

        history = report(host="a")["history"]
        self.assertEqual([run["mode"] for run in history], ["check", "check"])
        self.assertEqual(verdicts(history[0])["firewall"], "fail")
        self.assertEqual(verdicts(history[1])["firewall"], "pass")

    def test_report_per_host_lookup(self):
        check({"host": "win-01", "os": "windows", "version": "10.0", "items": WINDOWS_FULL})
        got = report(host="win-01")
        self.assertEqual(got["host"], "win-01")
        self.assertEqual(got["rate"], 1.0)
        self.assertEqual(got["not_applicable_items"], ["ssh_root_login", "selinux"])
        with self.assertRaises(ValueError):
            report(host="missing")

    def test_reset_clears_hosts(self):
        check({"host": "a", "os": "linux", "items": LINUX_FULL})
        self.assertEqual(reset(), {"hosts": 0})
        self.assertEqual(report()["hosts"], 0)

    def test_host_required(self):
        with self.assertRaises(ValueError):
            check({"os": "linux"})

    def test_os_required_and_validated(self):
        with self.assertRaises(ValueError):
            check({"host": "a"})
        with self.assertRaises(ValueError):
            check({"host": "a", "os": "solaris"})

    def test_recheck_requires_previous_result(self):
        with self.assertRaises(ValueError):
            check({"host": "ghost", "os": "linux"}, recheck=True)


if __name__ == "__main__":
    unittest.main()
