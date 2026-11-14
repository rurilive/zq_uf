import importlib
import threading
import time
import unittest

from accountrefresh import audit_log, get_token, refresh, report, stats

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


class RefreshPolicyTest(unittest.TestCase):
    """单飞、提前刷新、退避恢复、账号隔离和审计。"""

    def _reset(self):
        module._counts.clear()
        module._seq = 0
        module._states.clear()
        module._inflight.clear()
        module._reused.clear()
        module._audit.clear()
        module._token_source = None
        module._token_ttl = 0.0
        module.REFRESH_AHEAD = 30.0
        module.BACKOFF_BASE = 1.0
        module.BACKOFF_FACTOR = 2.0
        module.BACKOFF_MAX = 60.0
        module.ISOLATE_AFTER = 3

    setUp = _reset
    tearDown = _reset  # 恢复默认，避免影响上面的旧用例

    def test_concurrent_single_flight(self):
        calls = []

        def source(account):
            calls.append(account)
            deadline = time.monotonic() + 5
            while module._reused.get("acc", 0) < 7 and time.monotonic() < deadline:
                time.sleep(0.005)
            return "tok-acc", 100.0

        module._token_source = source
        results = []

        def worker():
            results.append(refresh("acc", force=True)["token"])

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(results), {"tok-acc"})
        self.assertEqual(module._reused["acc"], 7)
        self.assertEqual(report()["reused"]["acc"], 7)

    def test_early_refresh(self):
        module._token_ttl = 100.0
        first = get_token("a")
        self.assertTrue(first["refreshed"])
        second = get_token("a")
        self.assertFalse(second["refreshed"])
        self.assertEqual(first["token"], second["token"])
        self.assertEqual(stats()["a"], 1)
        # 把过期时间拨进提前量窗口内，应触发提前刷新
        module._states["a"]["expires_at"] = module._now() + 10.0
        third = get_token("a")
        self.assertTrue(third["refreshed"])
        self.assertNotEqual(first["token"], third["token"])
        self.assertEqual(stats()["a"], 2)

    def test_backoff_and_recovery(self):
        module.BACKOFF_BASE = 0.05
        failing = {"on": True}

        def source(account):
            if failing["on"]:
                raise RuntimeError("boom")
            return "good", 100.0

        module._token_source = source
        bad = refresh("a", force=True)
        self.assertIn("next_try", bad)
        self.assertEqual(stats()["a"], 1)
        # 退避期间不再尝试
        skipped = get_token("a")
        self.assertTrue(skipped["backoff"])
        self.assertEqual(stats()["a"], 1)
        self.assertIn("a", report()["backoff"])
        # 退避到期后恢复成功，失败计数清零
        failing["on"] = False
        module._states["a"]["next_try"] = module._now() - 1
        ok = get_token("a")
        self.assertTrue(ok["refreshed"])
        self.assertEqual(ok["token"], "good")
        self.assertEqual(module._states["a"]["failures"], 0)
        self.assertNotIn("a", report()["backoff"])

    def test_account_isolation(self):
        module.ISOLATE_AFTER = 2

        def source(account):
            if account == "bad":
                raise RuntimeError("risk control")
            return "tok-" + account, 100.0

        module._token_source = source
        refresh("bad", force=True)
        refresh("bad", force=True)
        self.assertIn("bad", report()["isolated"])
        # 隔离期间不再调度该账号
        blocked = get_token("bad")
        self.assertTrue(blocked["isolated"])
        self.assertEqual(stats()["bad"], 2)
        # 其他账号不受影响
        ok = get_token("good")
        self.assertTrue(ok["refreshed"])
        self.assertEqual(ok["token"], "tok-good")
        self.assertNotIn("good", report()["isolated"])

    def test_accounts_do_not_block_each_other(self):
        started = threading.Event()
        release = threading.Event()

        def source(account):
            if account == "slow":
                started.set()
                release.wait(2)
            return "tok-" + account, 100.0

        module._token_source = source
        t = threading.Thread(target=lambda: refresh("slow", force=True))
        t.start()
        self.assertTrue(started.wait(1))
        begin = time.monotonic()
        fast = refresh("fast", force=True)
        self.assertTrue(fast["refreshed"])
        self.assertLess(time.monotonic() - begin, 1)
        release.set()
        t.join()

    def test_audit_trail(self):
        module._token_ttl = 100.0
        get_token("a")
        get_token("a")

        def source(account):
            raise RuntimeError("x")

        module._token_source = source
        refresh("b", force=True)
        log = audit_log()
        actions = {(row["account"], row["action"]) for row in log}
        self.assertIn(("a", "refresh_success"), actions)
        self.assertIn(("a", "cache_hit"), actions)
        self.assertIn(("b", "refresh_failure"), actions)
        self.assertTrue(all("ts" in row for row in log))
