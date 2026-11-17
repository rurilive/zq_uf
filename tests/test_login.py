import unittest

from ssoflow import login, report, reset


class LoginResultTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_success_minimal(self):
        got = login({"user": "alice", "events": [{"app": "mail", "step": "password", "ok": True}]})
        self.assertTrue(got["ok"])
        self.assertEqual(got["reason"], "")
        self.assertEqual(got["stage"], "")
        self.assertEqual(got["action"], "")

    def test_user_required(self):
        with self.assertRaises(ValueError):
            login({"events": []})

    def test_bad_password_is_classified_at_authentication_stage(self):
        got = login({
            "user": "bob",
            "events": [{"app": "mail", "step": "password", "ok": False, "reason": "bad password"}],
        })
        self.assertFalse(got["ok"])
        self.assertEqual(got["stage"], "authentication")
        self.assertEqual(got["reason"], "bad_credential")
        self.assertEqual(got["action"], "retry_with_correct_password")

    def test_token_expired_default_reason(self):
        got = login({
            "user": "erin",
            "events": [{"app": "wiki", "step": "token", "ok": False}],
        })
        self.assertEqual(got["stage"], "token")
        self.assertEqual(got["reason"], "token_expired")
        self.assertEqual(got["action"], "reauthenticate")

    def test_account_locked(self):
        got = login({
            "user": "frank",
            "events": [{"app": "mail", "step": "password", "ok": False, "reason": "account locked"}],
        })
        self.assertEqual(got["reason"], "account_locked")
        self.assertEqual(got["action"], "contact_admin_to_unlock")


class MfaTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_mfa_failure_is_own_category(self):
        got = login({
            "user": "bob",
            "events": [
                {"app": "mail", "step": "password", "ok": True},
                {"app": "mail", "step": "mfa", "ok": False},
            ],
        })
        self.assertFalse(got["ok"])
        self.assertEqual(got["stage"], "mfa")
        self.assertEqual(got["reason"], "mfa_failed")
        self.assertEqual(len(got["abnormal"]), 1)
        self.assertEqual(got["abnormal"][0]["reason"], "mfa_failed")
        self.assertEqual(report()["mfa"]["failed"], 1)

    def test_mfa_bypass_detected_when_required_but_missing(self):
        got = login({
            "user": "gina",
            "events": [
                {"app": "mail", "step": "password", "ok": True, "mfa_required": True},
                {"app": "mail", "step": "session", "ok": True},
            ],
        })
        self.assertTrue(got["ok"])
        self.assertTrue(got["mfa_bypassed"])
        self.assertEqual(got["reason"], "mfa_bypassed")
        self.assertEqual(got["stage"], "mfa")
        self.assertEqual(report()["mfa"]["bypassed"], 1)
        self.assertEqual(got["abnormal"][0]["reason"], "mfa_bypassed")

    def test_explicit_bypass_flag(self):
        got = login({
            "user": "hank",
            "events": [{"app": "vpn", "step": "password", "ok": True, "mfa_bypassed": True}],
        })
        self.assertTrue(got["mfa_bypassed"])

    def test_no_bypass_when_mfa_present(self):
        got = login({
            "user": "ivy",
            "events": [
                {"app": "mail", "step": "password", "ok": True, "mfa_required": True},
                {"app": "mail", "step": "mfa", "ok": True},
            ],
        })
        self.assertFalse(got["mfa_bypassed"])
        self.assertEqual(got["abnormal"], [])


class ChainTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_chain_keeps_app_order_and_elapsed(self):
        got = login({
            "user": "carol",
            "events": [
                {"app": "mail", "step": "password", "ok": True, "ts": 1000},
                {"app": "mail", "step": "mfa", "ok": True, "ts": 1005},
                {"app": "vpn", "step": "token", "ok": True, "ts": 1030},
                {"app": "wiki", "step": "session", "ok": True, "ts": 1100},
            ],
        })
        apps = [hop["app"] for hop in got["chain"]]
        self.assertEqual(apps, ["mail", "vpn", "wiki"])
        self.assertEqual([hop["seq"] for hop in got["chain"]], [1, 2, 3])
        self.assertIsNone(got["chain"][0]["elapsed"])
        self.assertEqual(got["chain"][1]["elapsed"], 30)
        self.assertEqual(got["chain"][2]["elapsed"], 70)
        self.assertEqual(got["chain"][1]["time"], 1030)

    def test_chain_recorded_per_attempt_in_report(self):
        login({"user": "carol", "events": [
            {"app": "mail", "step": "password", "ok": True, "ts": 1},
            {"app": "vpn", "step": "mfa", "ok": True, "ts": 2},
        ]})
        chains = report()["chains"]
        self.assertEqual(len(chains), 1)
        self.assertEqual([hop["app"] for hop in chains[0]], ["mail", "vpn"])


class FailureIntervalTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_consecutive_failure_interval_recorded(self):
        first = login({"user": "judy", "events": [
            {"app": "mail", "step": "password", "ok": False, "ts": 2000}]})
        self.assertIsNone(first["failure_interval"])
        second = login({"user": "judy", "events": [
            {"app": "mail", "step": "password", "ok": False, "ts": 2150}]})
        self.assertEqual(second["failure_interval"], 150)
        third = login({"user": "judy", "events": [
            {"app": "vpn", "step": "mfa", "ok": False, "ts": 2200}]})
        self.assertEqual(third["failure_interval"], 50)

    def test_interval_is_per_account(self):
        login({"user": "judy", "events": [
            {"app": "mail", "step": "password", "ok": False, "ts": 2000}]})
        other = login({"user": "kyle", "events": [
            {"app": "mail", "step": "password", "ok": False, "ts": 2010}]})
        self.assertIsNone(other["failure_interval"])


class ReportTest(unittest.TestCase):
    def setUp(self):
        reset()

    def _seed(self):
        # alice：mail 密码错（authentication/bad_credential）
        login({"user": "alice", "events": [
            {"app": "mail", "step": "password", "ok": False, "reason": "bad password", "ts": 1767225600}]})
        # bob：mfa 失败（mfa/mfa_failed），同一账号两个应用各失败一次
        login({"user": "bob", "events": [
            {"app": "mail", "step": "password", "ok": True, "ts": 1767225700},
            {"app": "mail", "step": "mfa", "ok": False, "ts": 1767225710},
            {"app": "vpn", "step": "mfa", "ok": False, "ts": 1767225720},
        ]})
        # carol：成功跨 mail -> vpn
        login({"user": "carol", "events": [
            {"app": "mail", "step": "password", "ok": True, "ts": 1767312000},
            {"app": "vpn", "step": "mfa", "ok": True, "ts": 1767312060},
        ]})

    def test_reasons_bucketed_by_stage(self):
        self._seed()
        reasons = report()["reasons"]
        self.assertEqual(reasons["authentication"]["bad_credential"], 1)
        self.assertEqual(reasons["mfa"]["mfa_failed"], 2)

    def test_failures_counted_per_app_per_account(self):
        self._seed()
        by_app = report()["failures_by_app"]
        self.assertEqual(by_app["alice"]["mail"], 1)
        self.assertEqual(by_app["bob"]["mail"], 1)
        self.assertEqual(by_app["bob"]["vpn"], 1)
        self.assertNotIn("carol", by_app)

    def test_failures_rolled_up_by_day(self):
        self._seed()
        by_day = report()["failures_by_day"]
        # 1767225600 与 1767225710/20 同为 2026-01-01（UTC），共 3 次失败事件。
        self.assertEqual(by_day["2026-01-01"], 3)
        self.assertEqual(sum(by_day.values()), 3)

    def test_totals(self):
        self._seed()
        rep = report()
        self.assertEqual(rep["attempts"], 3)
        self.assertEqual(rep["failed"], 2)
        self.assertEqual(rep["users"], 3)
        self.assertEqual(rep["mfa"], {"failed": 2, "bypassed": 0})

    def test_filter_by_user(self):
        self._seed()
        rep = report(user="bob")
        self.assertTrue(all(item["user"] == "bob" for item in rep["abnormal"]))
        self.assertEqual(len(rep["abnormal"]), 2)
        # 聚合数字保持全局口径，不受过滤影响。
        self.assertEqual(rep["failed"], 2)

    def test_filter_by_app(self):
        self._seed()
        rep = report(app="vpn")
        self.assertTrue(all(item["app"] == "vpn" for item in rep["abnormal"]))
        # carol 的链路经过 vpn，也应被保留。
        chain_users = {chain[0]["user"] for chain in rep["chains"]}
        self.assertIn("carol", chain_users)

    def test_filter_by_time_window(self):
        self._seed()
        rep = report(since=1767300000, until=1767400000)
        # 窗口内只有 carol 的成功登录，没有异常项。
        self.assertEqual(rep["abnormal"], [])
        self.assertEqual(len(rep["chains"]), 1)

    def test_iso_time_filter_accepted(self):
        self._seed()
        rep = report(since="2026-01-02T00:00:00Z")
        self.assertEqual(rep["abnormal"], [])
        self.assertEqual(len(rep["chains"]), 1)

    def test_abnormal_replay_includes_context(self):
        self._seed()
        item = report(user="alice")["abnormal"][0]
        self.assertEqual(item["stage"], "authentication")
        self.assertEqual(item["reason"], "bad_credential")
        self.assertEqual(item["action"], "retry_with_correct_password")
        self.assertEqual(item["app"], "mail")
        self.assertIn("time", item)


if __name__ == "__main__":
    unittest.main()
