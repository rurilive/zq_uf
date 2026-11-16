import time
import unittest

from ssoflow import login, report, reset


class LoginTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_success(self):
        got = login({"user": "alice", "events": [{"app": "mail", "step": "password", "ok": True}]})
        self.assertTrue(got["ok"])
        self.assertEqual(got["reason"], "")
        self.assertEqual(got["stage"], "")

    def test_failure_reason_and_stage_classified(self):
        got = login({
            "user": "bob",
            "events": [
                {"app": "mail", "step": "password", "ok": True},
                {"app": "mail", "step": "mfa", "ok": False},
            ],
        })
        self.assertFalse(got["ok"])
        self.assertEqual(got["stage"], "mfa")
        self.assertEqual(got["reason"], "mfa failed")
        self.assertEqual(got["action"], "retry")

    def test_failure_reasons_per_stage(self):
        got = login({"user": "bob", "events": [{"app": "vpn", "step": "token", "ok": False}]})
        self.assertEqual(got["stage"], "token")
        self.assertEqual(got["reason"], "token expired")
        self.assertEqual(got["action"], "re-login")

        reset()
        got = login({"user": "bob", "events": [{"app": "vpn", "step": "account", "ok": False}]})
        self.assertEqual(got["reason"], "account locked")
        self.assertEqual(got["action"], "locked")

    def test_mfa_failure_is_separately_counted(self):
        login({
            "user": "bob",
            "events": [
                {"app": "mail", "step": "password", "ok": True},
                {"app": "mail", "step": "mfa", "ok": False},
            ],
        })
        mfa = report()["mfa"]
        self.assertEqual(mfa["failed"], 1)
        self.assertEqual(mfa["bypassed"], 0)

    def test_mfa_bypass_is_abnormal_and_counted(self):
        got = login({
            "user": "eve",
            "events": [
                {"app": "mail", "step": "password", "ok": True},
                {"app": "mail", "step": "mfa", "ok": True, "bypassed": True},
            ],
        })
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["abnormal"]), 1)
        self.assertEqual(got["abnormal"][0]["reason"], "mfa bypassed")
        self.assertEqual(report()["mfa"]["bypassed"], 1)

    def test_chain_keeps_app_order_and_elapsed(self):
        got = login({
            "user": "carol",
            "events": [
                {"app": "mail", "step": "password", "ok": True, "ts": 1000},
                {"app": "mail", "step": "mfa", "ok": True, "ts": 1005},
                {"app": "vpn", "step": "token", "ok": True, "ts": 1030},
                {"app": "wiki", "step": "password", "ok": True, "ts": 1080},
            ],
        })
        chain = got["chain"]
        self.assertEqual([hop["app"] for hop in chain], ["mail", "vpn", "wiki"])
        self.assertEqual([hop["seq"] for hop in chain], [0, 1, 2])
        self.assertEqual(chain[0]["from_app"], "")
        self.assertEqual(chain[1]["from_app"], "mail")
        self.assertEqual(chain[1]["elapsed"], 30)
        self.assertEqual(chain[2]["elapsed"], 50)
        # 同一应用内的环节顺序保留在 steps 里
        self.assertEqual([step["stage"] for step in chain[0]["steps"]], ["password", "mfa"])

    def test_failure_interval_between_attempts(self):
        login({"user": "bob", "events": [{"app": "mail", "step": "password", "ok": False, "ts": 1000}]})
        got = login({"user": "bob", "events": [{"app": "mail", "step": "password", "ok": False, "ts": 1042}]})
        self.assertEqual(got["events"][0]["interval"], 42)
        self.assertEqual(report()["intervals"]["bob"], [42])

    def test_report_reason_buckets_by_stage(self):
        login({"user": "dave", "events": [{"app": "vpn", "step": "mfa", "ok": False}]})
        login({"user": "erin", "events": [{"app": "vpn", "step": "password", "ok": False}]})
        reasons = report()["reasons"]
        self.assertEqual(reasons["mfa"]["mfa failed"], 1)
        self.assertEqual(reasons["password"]["bad password"], 1)

    def test_failures_counted_per_app_per_user(self):
        login({
            "user": "dave",
            "events": [
                {"app": "vpn", "step": "mfa", "ok": False},
                {"app": "mail", "step": "password", "ok": False},
                {"app": "vpn", "step": "token", "ok": False},
            ],
        })
        by_app = report()["failures_by_app"]["dave"]
        self.assertEqual(by_app, {"vpn": 2, "mail": 1})
        self.assertEqual(report()["by_user"]["dave"]["failures_by_app"], {"vpn": 2, "mail": 1})

    def test_failures_summary_by_day(self):
        login({"user": "dave", "events": [
            {"app": "vpn", "step": "mfa", "ok": False, "ts": "2026-11-17T01:00:00+00:00"},
            {"app": "vpn", "step": "mfa", "ok": False, "ts": "2026-11-17T09:00:00+00:00"},
            {"app": "vpn", "step": "mfa", "ok": False, "ts": "2026-11-18T02:00:00+00:00"},
        ]})
        by_day = report()["failures_by_day"]
        self.assertEqual(by_day, {"2026-11-17": 2, "2026-11-18": 1})

    def test_report_filter_by_user_app_time(self):
        login({"user": "dave", "events": [
            {"app": "vpn", "step": "mfa", "ok": False, "ts": 1000},
            {"app": "mail", "step": "password", "ok": True, "ts": 1010},
        ]})
        login({"user": "frank", "events": [
            {"app": "vpn", "step": "password", "ok": False, "ts": 2000},
        ]})

        only_dave = report(user="dave")
        self.assertEqual(set(only_dave["by_user"]), {"dave"})
        self.assertEqual(len(only_dave["abnormal"]), 1)

        only_vpn = report(app="vpn")
        self.assertTrue(all(
            item["app"] == "vpn" for item in only_vpn["abnormal"]
        ))
        # 链路保留会话的完整跳转路径，只筛选出涉及该应用的会话
        self.assertTrue(all(
            any(hop["app"] == "vpn" for hop in entry["chain"])
            for entry in only_vpn["chains"]
        ))

        window = report(since=1500)
        self.assertEqual(len(window["abnormal"]), 1)
        self.assertEqual(window["abnormal"][0]["user"], "frank")

        window2 = report(until=1500)
        self.assertEqual(len(window2["abnormal"]), 1)
        self.assertEqual(window2["abnormal"][0]["user"], "dave")

    def test_history_kept_across_attempts(self):
        login({"user": "bob", "events": [{"app": "mail", "step": "password", "ok": False, "ts": 1000}]})
        login({"user": "bob", "events": [{"app": "mail", "step": "password", "ok": True, "ts": 2000}]})
        self.assertEqual(report()["by_user"]["bob"]["attempts"], 2)
        self.assertEqual(report()["by_user"]["bob"]["failed"], 1)
        # 异常登录可按时间回看
        got = report(user="bob", since=900, until=1500)
        self.assertEqual(len(got["abnormal"]), 1)
        self.assertEqual(got["abnormal"][0]["reason"], "bad password")

    def test_user_required(self):
        with self.assertRaises(ValueError):
            login({"events": []})

    def test_reset(self):
        login({"user": "bob", "events": [{"app": "mail", "step": "mfa", "ok": False}]})
        self.assertEqual(reset(), {"users": 0})
        self.assertEqual(report()["users"], 0)

    def test_default_ts_filled(self):
        before = time.time()
        got = login({"user": "gina", "events": [{"app": "mail", "step": "password", "ok": True}]})
        self.assertGreaterEqual(got["events"][0]["ts"], before)
