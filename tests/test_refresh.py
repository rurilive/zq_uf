import importlib
import unittest
import json
import os
import tempfile
import threading

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


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _ModuleTest(unittest.TestCase):
    """每个用例前后都还原模块全局状态，避免污染旧测试。"""

    def setUp(self):
        module.reset()
        self.clock = _Clock()
        module._now = self.clock

    def tearDown(self):
        module.reset()


class SingleflightTest(_ModuleTest):
    def setUp(self):
        super().setUp()

    def test_concurrent_singleflight_one_refresh(self):
        release = threading.Event()
        calls = []

        def backend(account):
            calls.append(account)
            release.wait(2.0)
            return {"account": account, "token": "shared", "expires_in": 100}

        module.configure(backend=backend)
        results = []
        threads = [
            threading.Thread(
                target=lambda: results.append(refresh("acc")))
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        while not module.account_status("acc")["in_flight"]:
            pass
        release.set()
        for thread in threads:
            thread.join()

        self.assertEqual(len(calls), 1)
        self.assertEqual(stats()["acc"], 1)
        self.assertEqual({item["token"] for item in results}, {"shared"})
        self.assertEqual(module.report()["wait_reuse"]["acc"], 7)

    def test_accounts_do_not_block_each_other(self):
        block_slow = threading.Event()

        def backend(account):
            if account == "slow":
                block_slow.wait(2.0)
            return {"account": account, "token": account + "-t"}

        module.configure(backend=backend)
        slow = threading.Thread(target=refresh, args=("slow",))
        slow.start()
        while not module.account_status("slow")["in_flight"]:
            pass

        fast = threading.Thread(target=refresh, args=("fast",))
        fast.start()
        fast.join(0.5)
        self.assertFalse(fast.is_alive())
        block_slow.set()
        slow.join()
        self.assertEqual(module.weight("fast"), 1.0)


class EarlyRefreshTest(_ModuleTest):
    def setUp(self):
        super().setUp()

    def test_refresh_only_within_lead_time(self):
        module.configure(
            backend=lambda account: {"token": "t@%d" % self.clock.t,
                                     "expires_in": 100},
            lead_time=30,
        )

        first = get_token("acc")
        self.clock.t = 20.0
        self.assertEqual(get_token("acc")["token"], first["token"])
        self.assertEqual(stats()["acc"], 1)

        self.clock.t = 70.0  # 距到期 30s，进入提前量窗口
        second = get_token("acc")
        self.assertNotEqual(second["token"], first["token"])
        self.assertEqual(stats()["acc"], 2)

    def test_force_refresh_ignores_fresh_cache(self):
        module.configure(
            backend=lambda account: {"token": "t@%d" % self.clock.t,
                                     "expires_in": 100},
            lead_time=30,
        )
        first = refresh("acc")
        self.clock.t = 1.0
        forced = refresh("acc", force=True)
        self.assertNotEqual(forced["token"], first["token"])
        self.assertEqual(stats()["acc"], 2)


class BackoffRecoveryTest(_ModuleTest):
    def setUp(self):
        super().setUp()
        module.configure(backoff_base=1.0, backoff_factor=2.0,
                         backoff_cap=10.0)

    def test_exponential_backoff_then_recovery(self):
        attempts = {"n": 0}

        def backend(account):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise RuntimeError("boom #%d" % attempts["n"])
            return {"account": account, "token": "good", "expires_in": 100}

        module.configure(backend=backend)

        first = refresh("acc")
        self.assertFalse(first["ok"])
        self.assertEqual(first["next_try"], 1.0)

        skipped = refresh("acc")  # 退避窗口内，不打后端
        self.assertTrue(skipped["backoff"])
        self.assertEqual(attempts["n"], 1)
        self.assertEqual(skipped["next_try"], 1.0)

        self.clock.t = 1.0
        second = refresh("acc")
        self.assertFalse(second["ok"])
        self.assertEqual(second["next_try"], 3.0)  # 1 + 1*2^1

        self.clock.t = 3.0
        recovered = refresh("acc")
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["token"], "good")
        self.assertIsNone(module.next_try_at("acc"))
        self.assertEqual(module.account_status("acc")["failures"], 0)
        self.assertEqual(attempts["n"], 3)

    def test_force_bypasses_backoff_window(self):
        def backend(account):
            raise RuntimeError("always fail")

        module.configure(backend=backend)
        refresh("acc")
        forced = refresh("acc", force=True)
        self.assertFalse(forced["backoff"])
        self.assertEqual(module.account_status("acc")["failures"], 2)


class IsolationTest(_ModuleTest):
    def setUp(self):
        super().setUp()
        module.configure(backoff_base=1.0, backoff_factor=2.0,
                         backoff_cap=10.0, isolate_threshold=3)

    def test_isolated_after_threshold_and_recovers(self):
        def failing(account):
            raise RuntimeError("locked")

        module.configure(backend=failing)
        for index in range(3):
            self.clock.t = float(index) * 10
            refresh("bad")

        self.assertTrue(module.is_isolated("bad"))
        self.assertEqual(module.weight("bad"), module.ISOLATED_WEIGHT)
        self.assertIn("bad", module.report()["isolated"])

        def succeeding(account):
            return {"account": account, "token": "alive", "expires_in": 100}

        module.configure(backend=succeeding)
        self.clock.t = 100.0
        result = refresh("bad")
        self.assertTrue(result["ok"])
        self.assertFalse(module.is_isolated("bad"))
        self.assertEqual(module.weight("bad"), module.NORMAL_WEIGHT)
        self.assertEqual(module.report()["isolated"], [])

    def test_failed_account_isolated_alone(self):
        def backend(account):
            if account == "bad":
                raise RuntimeError("locked")
            return {"account": account, "token": "fine", "expires_in": 100}

        module.configure(backend=backend)
        for index in range(3):
            self.clock.t = float(index) * 10
            refresh("bad")
        healthy = get_token("good")

        self.assertTrue(healthy["ok"])
        self.assertFalse(module.is_isolated("good"))
        self.assertEqual(module.weight("good"), 1.0)


class AuditTest(_ModuleTest):
    def setUp(self):
        super().setUp()

    def test_audit_records_lifecycle(self):
        attempts = {"n": 0}

        def backend(account):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("first fails")
            return {"account": account, "token": "ok", "expires_in": 100}

        module.configure(backend=backend)
        refresh("acc")
        self.clock.t = 2.0
        refresh("acc")

        events = [record["event"] for record in module.audit_records()]
        self.assertIn("refresh_start", events)
        self.assertIn("refresh_failure", events)
        self.assertIn("refresh_success", events)
        failure = next(record for record in module.audit_records()
                       if record["event"] == "refresh_failure")
        self.assertEqual(failure["account"], "acc")
        self.assertEqual(failure["error"], "first fails")
        self.assertIn("next_try", failure)

    def test_audit_file_and_wait_event(self):
        release = threading.Event()

        def backend(account):
            release.wait(2.0)
            return {"account": account, "token": "x", "expires_in": 100}

        with tempfile.NamedTemporaryFile("r", suffix=".jsonl",
                                         delete=False) as handle:
            path = handle.name
        self.addCleanup(os.unlink, path)
        module.configure(backend=backend, audit_file=path)
        threads = [threading.Thread(target=refresh, args=("acc",))
                   for _ in range(3)]
        for thread in threads:
            thread.start()
        while not module.account_status("acc")["in_flight"]:
            pass
        release.set()
        for thread in threads:
            thread.join()

        with open(path, encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle
                     if line.strip()]
        events = {line["event"] for line in lines}
        self.assertIn("wait_reuse", events)
        self.assertIn("refresh_success", events)
        wait_record = next(line for line in lines
                           if line["event"] == "wait_reuse")
        self.assertTrue(wait_record["waited"])
