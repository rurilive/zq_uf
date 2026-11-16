import unittest

from hostbaseline import check, report, reset
from hostbaseline.rules import RULES, applies


class BaselineTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_full_match_passes(self):
        got = check({
            "host": "lnx-01",
            "os": "linux",
            "os_version": "8",
            "items": {"firewall": "on", "antivirus": "on", "ssh_root_login": "no"},
        })
        self.assertEqual([r["item"] for r in got["failed"]], [])
        self.assertEqual(
            [r["item"] for r in got["passed"]],
            ["firewall", "antivirus", "ssh_root_login"],
        )

    def test_result_carries_expected_actual_and_verdict(self):
        got = check({
            "host": "lnx-01",
            "os": "linux",
            "os_version": "8",
            "items": {"firewall": "off", "antivirus": "on", "ssh_root_login": "no"},
        })
        result = next(r for r in got["failed"] if r["item"] == "firewall")
        self.assertEqual(result["expected"], "on")
        self.assertEqual(result["actual"], "off")
        self.assertEqual(result["verdict"], "fail")

    def test_windows_ssh_is_not_applicable(self):
        got = check({
            "host": "win-01",
            "os": "windows",
            "os_version": "11",
            "items": {"firewall": "on", "antivirus": "on",
                      "bitlocker": "on", "guest_account": "disabled"},
        })
        self.assertEqual(
            [r["item"] for r in got["not_applicable"]],
            ["ssh_root_login"],
        )
        self.assertEqual(got["failed"], [])

    def test_version_not_in_scope_is_not_applicable(self):
        # bitlocker 只覆盖 windows 10/11/2019/2022
        got = check({
            "host": "win-old",
            "os": "windows",
            "os_version": "7",
            "items": {"firewall": "on", "antivirus": "on", "guest_account": "disabled"},
        })
        self.assertIn("bitlocker", [r["item"] for r in got["not_applicable"]])
        self.assertNotIn("bitlocker", [r["item"] for r in got["failed"]])

    def test_missing_version_does_not_exclude_versioned_rule(self):
        ssh = next(r for r in RULES if r["name"] == "ssh_root_login")
        self.assertTrue(applies(ssh, "linux", None))
        got = check({
            "host": "lnx-x",
            "os": "linux",
            "items": {"firewall": "on", "antivirus": "on", "ssh_root_login": "no"},
        })
        na_items = [r["item"] for r in got["not_applicable"]]
        # ssh_root_login 是版本受限的 Linux 规则，版本缺失时仍适用
        self.assertNotIn("ssh_root_login", na_items)
        # Windows 专属规则在 Linux 上仍判不适用
        self.assertIn("bitlocker", na_items)

    def test_applies_helper(self):
        ssh = next(r for r in RULES if r["name"] == "ssh_root_login")
        self.assertTrue(applies(ssh, "linux", "9"))
        self.assertFalse(applies(ssh, "linux", "12"))
        self.assertFalse(applies(ssh, "windows", "11"))
        fw = next(r for r in RULES if r["name"] == "firewall")
        self.assertTrue(applies(fw, "windows", "11"))

    def test_report_excludes_not_applicable_from_rate(self):
        check({
            "host": "win-01",
            "os": "windows",
            "os_version": "11",
            "items": {"firewall": "on", "antivirus": "off",
                      "bitlocker": "on", "guest_account": "disabled"},
        })
        got = report()
        # 适用 4 项（ssh 不适用），通过 3 项
        self.assertEqual(got["checks"], 4)
        self.assertEqual(got["passed"], 3)
        self.assertEqual(got["not_applicable"], 1)
        self.assertEqual(got["rate"], 0.75)

    def test_remediation_list_has_host_item_expected(self):
        check({
            "host": "win-01",
            "os": "windows",
            "os_version": "11",
            "items": {"firewall": "off", "antivirus": "off"},
        })
        items = {(r["host"], r["item"]) for r in report()["remediation"]}
        self.assertIn(("win-01", "firewall"), items)
        self.assertIn(("win-01", "antivirus"), items)
        entry = next(r for r in report()["remediation"] if r["item"] == "firewall")
        self.assertEqual(entry["expected"], "on")

    def test_recheck_only_runs_failed_items(self):
        check({
            "host": "lnx-01",
            "os": "linux",
            "os_version": "8",
            "items": {"firewall": "off", "antivirus": "on", "ssh_root_login": "yes"},
        })
        got = check({
            "host": "lnx-01",
            "items": {"firewall": "on", "antivirus": "on", "ssh_root_login": "yes"},
        }, recheck=True)
        self.assertEqual(
            sorted(r["item"] for r in got["recheck"]),
            ["firewall", "ssh_root_login"],
        )
        firewall = next(r for r in got["recheck"] if r["item"] == "firewall")
        self.assertEqual(firewall["first_verdict"], "fail")
        self.assertEqual(firewall["verdict"], "pass")
        self.assertTrue(firewall["changed"])
        ssh = next(r for r in got["recheck"] if r["item"] == "ssh_root_login")
        self.assertEqual(ssh["verdict"], "fail")
        self.assertFalse(ssh["changed"])

    def test_recheck_updates_host_state_and_history(self):
        check({
            "host": "lnx-01",
            "os": "linux",
            "os_version": "8",
            "items": {"firewall": "off", "antivirus": "on", "ssh_root_login": "no"},
        })
        check({"host": "lnx-01", "items": {"firewall": "on"}}, recheck=True)
        got = report(host="lnx-01")
        self.assertEqual([r["item"] for r in got["failed"]], [])
        self.assertEqual(len(got["history"]), 2)
        self.assertEqual(got["history"][0]["mode"], "full")
        self.assertEqual(got["history"][1]["mode"], "recheck")
        # 上一次结果可回溯
        self.assertEqual(got["previous"]["mode"], "full")

    def test_recheck_without_full_check_raises(self):
        with self.assertRaises(ValueError):
            check({"host": "ghost", "items": {"firewall": "on"}}, recheck=True)

    def test_host_required(self):
        with self.assertRaises(ValueError):
            check({"os": "linux"})

    def test_report_unknown_host_raises(self):
        with self.assertRaises(ValueError):
            report(host="nobody")


if __name__ == "__main__":
    unittest.main()
