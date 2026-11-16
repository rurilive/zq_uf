import unittest

from ssoflow import login, report, reset


class LoginTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_success(self):
        got = login({"user": "alice", "events": [{"app": "mail", "step": "password", "ok": True}]})
        self.assertTrue(got["ok"])

    def test_mfa_failure_is_generic(self):
        got = login({
            "user": "bob",
            "events": [
                {"app": "mail", "step": "password", "ok": True},
                {"app": "mail", "step": "mfa", "ok": False},
            ],
        })
        self.assertEqual(got["reason"], "login failed")
        self.assertEqual(got["stage"], "")

    def test_no_chain(self):
        got = login({
            "user": "carol",
            "events": [
                {"app": "mail", "step": "password", "ok": True},
                {"app": "vpn", "step": "mfa", "ok": True},
            ],
        })
        self.assertEqual(got["chain"], [])

    def test_report_has_no_reason_buckets(self):
        login({"user": "dave", "events": [{"app": "vpn", "step": "mfa", "ok": False}]})
        self.assertEqual(report()["reasons"], {})

    def test_user_required(self):
        with self.assertRaises(ValueError):
            login({"events": []})
