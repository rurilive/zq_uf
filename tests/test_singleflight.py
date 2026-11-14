import importlib
import json
import os
import tempfile
import threading
import time
import unittest

from accountrefresh import audit_log, get_token, refresh, report, set_issuer, stats

module = importlib.import_module("accountrefresh.refresh")


class SingleFlightTest(unittest.TestCase):
    def setUp(self):
        module._counts.clear()
        module._waits.clear()
        module._accounts.clear()
        module._audit.clear()
        module._seq = 0
        self._saved = (module.EARLY_REFRESH_S, module.BACKOFF_BASE_S,
                       module.BACKOFF_MAX_S, module.ISOLATE_AFTER,
                       module.ISOLATION_S)

    def tearDown(self):
        (module.EARLY_REFRESH_S, module.BACKOFF_BASE_S, module.BACKOFF_MAX_S,
         module.ISOLATE_AFTER, module.ISOLATION_S) = self._saved
        module._issuer = None
        module._audit_path = None

    def test_concurrent_single_flight(self):
        calls = []

        def issuer(account):
            calls.append(account)
            time.sleep(0.2)
            return {"token": "tok-shared", "expires_in": 3600}

        set_issuer(issuer)
        barrier = threading.Barrier(8)
        results = []

        def worker():
            barrier.wait()
            results.append(get_token("acc-x")["token"])

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(calls), 1)
        self.assertEqual(stats()["acc-x"], 1)
        self.assertEqual(set(results), {"tok-shared"})
        self.assertGreaterEqual(report()["waits"]["acc-x"], 1)

    def test_early_refresh_before_expiry(self):
        n = [0]

        def short_lived(account):
            n[0] += 1
            return {"token": "t%d" % n[0], "expires_in": 10}

        set_issuer(short_lived)
        first = get_token("acc-e")["token"]
        second = get_token("acc-e")["token"]
        self.assertNotEqual(first, second)  # 距过期不足提前量，提前刷新
        self.assertEqual(stats()["acc-e"], 2)

        def long_lived(account):
            n[0] += 1
            return {"token": "t%d" % n[0], "expires_in": 3600}

        set_issuer(long_lived)
        first = get_token("acc-f")["token"]
        second = get_token("acc-f")["token"]
        self.assertEqual(first, second)  # 有效期内复用
        self.assertEqual(stats()["acc-f"], 1)

    def test_backoff_and_recovery(self):
        module.BACKOFF_BASE_S = 0.05
        attempts = [0]

        def flaky(account):
            attempts[0] += 1
            if attempts[0] == 1:
                raise RuntimeError("boom")
            return {"token": "tok-ok", "expires_in": 3600}

        set_issuer(flaky)
        failed = refresh("acc-b")
        self.assertIn("next_try", failed)
        self.assertIn("error", failed)

        refused = refresh("acc-b")
        self.assertTrue(refused["backoff"])
        self.assertEqual(attempts[0], 1)  # 退避期内不再尝试
        self.assertIn("acc-b", report()["backoff"])

        time.sleep(0.08)  # 等过退避窗口
        recovered = refresh("acc-b")
        self.assertTrue(recovered["refreshed"])
        self.assertEqual(recovered["token"], "tok-ok")
        self.assertNotIn("next_try", recovered)
        self.assertNotIn("acc-b", report()["backoff"])

    def test_failure_isolation_and_weight(self):
        module.ISOLATE_AFTER = 3
        module.ISOLATION_S = 60.0

        def issuer(account):
            if account == "bad":
                raise RuntimeError("risk control")
            return {"token": "good-tok", "expires_in": 3600}

        set_issuer(issuer)
        for _ in range(3):
            refresh("bad", force=True)

        rep = report()
        self.assertIn("bad", rep["isolated"])
        self.assertEqual(rep["weights"]["bad"], 0.0)

        blocked = refresh("bad")
        self.assertTrue(blocked["isolated"])

        ok = get_token("good")  # 失败账号不影响其他账号
        self.assertTrue(ok["refreshed"])
        self.assertEqual(ok["token"], "good-tok")
        self.assertNotIn("good", rep["isolated"])

    def test_accounts_do_not_block_each_other(self):
        def issuer(account):
            if account == "slow":
                time.sleep(0.4)
            return {"token": account + "-tok", "expires_in": 3600}

        set_issuer(issuer)
        slow = threading.Thread(target=get_token, args=("slow",))
        slow.start()
        time.sleep(0.05)  # 确保 slow 已在刷新中
        start = time.monotonic()
        result = get_token("fast")
        elapsed = time.monotonic() - start
        slow.join()

        self.assertEqual(result["token"], "fast-tok")
        self.assertLess(elapsed, 0.3)
        self.assertEqual(stats()["fast"], 1)
        self.assertEqual(stats()["slow"], 1)

    def test_audit_records(self):
        def issuer(account):
            if account == "acc-fail":
                raise RuntimeError("denied")
            return {"token": "tok-a", "expires_in": 3600}

        set_issuer(issuer)
        refresh("acc-ok")
        refresh("acc-fail")

        log = audit_log()
        ok = [e for e in log if e["event"] == "refresh_ok"
              and e["account"] == "acc-ok"]
        bad = [e for e in log if e["event"] == "refresh_failed"
               and e["account"] == "acc-fail"]
        self.assertTrue(ok)
        self.assertTrue(bad)
        self.assertTrue(all("ts" in e for e in ok + bad))
        self.assertIn("next_try", bad[0])

    def test_audit_file_sink(self):
        set_issuer(lambda account: {"token": "tok-f", "expires_in": 3600})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audit.log")
            module.set_audit_file(path)
            refresh("acc-file")
            module.set_audit_file(None)
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["event"], "refresh_ok")
        self.assertEqual(entry["account"], "acc-file")


if __name__ == "__main__":
    unittest.main()
