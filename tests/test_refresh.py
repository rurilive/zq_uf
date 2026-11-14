import importlib
import threading
import time
import unittest

from accountrefresh import audit_log, configure, get_token, refresh, reset, stats, status

module = importlib.import_module("accountrefresh.refresh")


class RefreshTest(unittest.TestCase):
    def setUp(self):
        module._counts.clear()
        module._seq = 0

    def test_get_token_refreshes(self):
        first = get_token("a")["token"]
        second = get_token("a")["token"]
        self.assertNotEqual(first, second)

    def test_every_call_counts(self):
        get_token("a")
        get_token("a")
        self.assertEqual(stats()["a"], 2)

    def test_no_backoff(self):
        self.assertNotIn("next_try", refresh("a"))


class SingleFlightTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.clock = [1000.0]
        configure(clock=lambda: self.clock[0])

    def tearDown(self):
        reset()

    def test_concurrent_single_flight(self):
        calls = []
        gate = threading.Event()

        def issuer(account):
            calls.append(account)
            gate.wait(5)
            return {"token": "tok-1", "expires_in": 300}

        configure(issuer=issuer)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(get_token("acc")))
            for _ in range(5)
        ]
        for thread in threads:
            thread.start()
        time.sleep(0.3)
        gate.set()
        for thread in threads:
            thread.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual({r["token"] for r in results}, {"tok-1"})
        self.assertEqual(status()["reused"]["acc"], 4)

    def test_refresh_ahead_of_expiry(self):
        issued = []

        def issuer(account):
            token = "t%d" % len(issued)
            issued.append(token)
            return {"token": token, "expires_in": 100}

        configure(issuer=issuer, refresh_ahead=30)
        first = get_token("acc")["token"]
        self.clock[0] += 69
        self.assertEqual(get_token("acc")["token"], first)
        self.assertEqual(len(issued), 1)
        self.clock[0] += 2
        second = get_token("acc")["token"]
        self.assertNotEqual(second, first)
        self.assertEqual(len(issued), 2)

    def test_backoff_and_recovery(self):
        attempts = []

        def issuer(account):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("risk control")
            return {"token": "tok-ok", "expires_in": 100}

        configure(issuer=issuer, backoff_base=10, backoff_max=100)
        first = get_token("acc")
        self.assertIsNone(first["token"])
        self.assertEqual(first["next_try"], 1010.0)
        second = get_token("acc")
        self.assertEqual(second["error"], "backoff")
        self.assertEqual(len(attempts), 1)
        self.clock[0] = 1011.0
        third = get_token("acc")
        self.assertEqual(third["token"], "tok-ok")
        self.assertTrue(third["refreshed"])
        self.assertEqual(status()["backoff"], {})

    def test_account_isolation(self):
        def issuer(account):
            if account == "bad":
                raise RuntimeError("locked")
            return {"token": "tok-" + account, "expires_in": 100}

        configure(issuer=issuer, isolate_threshold=2, backoff_base=10)
        get_token("bad")
        self.clock[0] += 11
        get_token("bad")
        current = status()
        self.assertIn("bad", current["isolated"])
        self.assertLess(current["weights"]["bad"], 1.0)
        self.assertEqual(get_token("good")["token"], "tok-good")
        self.assertNotIn("good", current["isolated"])

    def test_accounts_do_not_block_each_other(self):
        gate = threading.Event()

        def issuer(account):
            if account == "slow":
                gate.wait(5)
                return {"token": "tok-slow", "expires_in": 100}
            return {"token": "tok-fast", "expires_in": 100}

        configure(issuer=issuer)
        slow = threading.Thread(target=lambda: get_token("slow"))
        slow.start()
        time.sleep(0.1)
        self.assertEqual(get_token("fast")["token"], "tok-fast")
        gate.set()
        slow.join()

    def test_audit_trail(self):
        def issuer(account):
            if account == "bad":
                raise RuntimeError("locked")
            return {"token": "tok", "expires_in": 100}

        configure(issuer=issuer)
        get_token("good")
        get_token("bad")
        events = [(r["event"], r["account"]) for r in audit_log()]
        self.assertIn(("refresh_success", "good"), events)
        self.assertIn(("refresh_failure", "bad"), events)
