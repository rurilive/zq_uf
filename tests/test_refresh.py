import importlib
import unittest

from accountrefresh import get_token, refresh, stats

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

import threading
import time

from accountrefresh import audit_log


class AccountRefreshTest(unittest.TestCase):
    def setUp(self):
        module._reset()
        self._saved = (module.TOKEN_TTL, module.REFRESH_AHEAD,
                       module.BACKOFF_BASE, module.BACKOFF_MAX,
                       module._token_source)

    def tearDown(self):
        (module.TOKEN_TTL, module.REFRESH_AHEAD,
         module.BACKOFF_BASE, module.BACKOFF_MAX,
         module._token_source) = self._saved
        module._reset()

    def test_concurrent_single_flight(self):
        module.TOKEN_TTL = 60.0
        calls = []
        entered = threading.Event()
        gate = threading.Event()

        def source(account):
            calls.append(account)
            entered.set()
            gate.wait(5)
            return {"token": "tok-1", "expires_in": 60.0}

        module._token_source = source
        results = []
        threads = [threading.Thread(
            target=lambda: results.append(get_token("acc")["token"]))
            for _ in range(5)]
        for t in threads:
            t.start()
        self.assertTrue(entered.wait(2))
        time.sleep(0.3)
        gate.set()
        for t in threads:
            t.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual(results, ["tok-1"] * 5)
        self.assertEqual(stats()["wait_reused"]["acc"], 4)

    def test_refresh_ahead_of_expiry(self):
        module.TOKEN_TTL = 100.0
        module.REFRESH_AHEAD = 30.0
        first = get_token("acc")["token"]
        self.assertEqual(get_token("acc")["token"], first)
        self.assertEqual(module._counts["acc"], 1)
        module.REFRESH_AHEAD = 200.0
        second = get_token("acc")["token"]
        self.assertNotEqual(second, first)
        self.assertEqual(module._counts["acc"], 2)

    def test_backoff_and_recover(self):
        module.BACKOFF_BASE = 0.01
        fails = {"n": 0}

        def source(account):
            fails["n"] += 1
            raise RuntimeError("boom")

        module._token_source = source
        for _ in range(2):
            self.assertRaises(RuntimeError, get_token, "acc")
            time.sleep(0.02)
        bo = stats()["backoff"]["acc"]
        self.assertEqual(bo["failures"], 2)
        module._backoff["acc"]["next_try"] = time.time() + 60
        self.assertRaises(RuntimeError, get_token, "acc")
        self.assertEqual(fails["n"], 2)
        module._backoff["acc"]["next_try"] = 0
        module._token_source = lambda account: {"token": "ok", "expires_in": 0}
        self.assertEqual(get_token("acc")["token"], "ok")
        self.assertNotIn("acc", stats()["backoff"])

    def test_isolation_and_weight(self):
        module.BACKOFF_BASE = 0.0

        def source(account):
            if account == "bad":
                raise RuntimeError("risk control")
            return {"token": "ok-" + account, "expires_in": 0}

        module._token_source = source
        for _ in range(3):
            self.assertRaises(RuntimeError, get_token, "bad")
        self.assertIn("bad", stats()["isolated"])
        self.assertEqual(stats()["weights"]["bad"], module.ISOLATED_WEIGHT)
        self.assertEqual(get_token("good")["token"], "ok-good")
        self.assertEqual(stats()["weights"]["good"], 1.0)

    def test_accounts_do_not_block_each_other(self):
        module.TOKEN_TTL = 60.0
        entered = threading.Event()
        gate = threading.Event()

        def source(account):
            if account == "slow":
                entered.set()
                gate.wait(5)
                return {"token": "slow-tok", "expires_in": 60.0}
            return {"token": "fast-tok", "expires_in": 60.0}

        module._token_source = source
        t = threading.Thread(target=lambda: get_token("slow"))
        t.start()
        self.assertTrue(entered.wait(2))
        self.assertEqual(get_token("fast")["token"], "fast-tok")
        gate.set()
        t.join()
        self.assertEqual(get_token("slow")["token"], "slow-tok")

    def test_audit_log(self):
        module.BACKOFF_BASE = 0.0

        def source(account):
            if account == "bad":
                raise RuntimeError("x")
            return {"token": "t", "expires_in": 0}

        module._token_source = source
        get_token("good")
        for _ in range(3):
            self.assertRaises(RuntimeError, get_token, "bad")
        events = [(r["account"], r["event"]) for r in audit_log()]
        self.assertIn(("good", "refresh_success"), events)
        self.assertIn(("bad", "refresh_failure"), events)
        self.assertIn(("bad", "isolated"), events)
