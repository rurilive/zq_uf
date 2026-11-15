import threading
import time
import unittest

from loginkeep import ACCOUNT, SITE, LoginError, LoginPool, lease, refresh


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class Auth:
    """可编程登录器。"""

    def __init__(self, ttl=1800, error=None):
        self.ttl = ttl
        self.error = error
        self.calls = 0
        self.gate = None

    def __call__(self, account):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return {"token": "tok-%s-%d" % (account, self.calls), "ttl": self.ttl}


def make_pool(auth=None, **kw):
    clock = kw.pop("clock", Clock())
    return LoginPool(auth or Auth(), clock=clock, **kw), clock


class LeaseTest(unittest.TestCase):
    def test_same_account_shares_token(self):
        pool, _ = make_pool()
        a = pool.lease("acc")
        b = pool.lease("acc")
        self.assertEqual(a["token"], b["token"])
        self.assertTrue(a["shared"])
        self.assertGreater(a["expires_in"], 0)

    def test_one_login_for_many_leases(self):
        auth = Auth()
        pool, _ = make_pool(auth)
        pool.lease("acc")
        pool.lease("acc")
        pool.lease("acc")
        self.assertEqual(auth.calls, 1)

    def test_distinct_accounts_get_distinct_tokens(self):
        pool, _ = make_pool()
        self.assertNotEqual(pool.lease("a")["token"], pool.lease("b")["token"])

    def test_module_level_lease_is_shared(self):
        self.assertTrue(lease("m-acc")["shared"])
        self.assertEqual(lease("m-acc")["token"], lease("m-acc")["token"])


class RefreshTest(unittest.TestCase):
    def test_refresh_early_is_noop(self):
        auth = Auth(ttl=1800)
        pool, _ = make_pool(auth, refresh_margin=60)
        tok = pool.lease("acc")["token"]
        r = pool.refresh(tok)
        self.assertFalse(r["refreshed"])
        self.assertEqual(r["token"], tok)
        self.assertEqual(auth.calls, 1)

    def test_refresh_near_expiry_issues_new_shared_token(self):
        auth = Auth(ttl=1800)
        pool, clock = make_pool(auth, refresh_margin=60)
        tok = pool.lease("acc")["token"]
        clock.advance(1800 - 30)
        r = pool.refresh(tok)
        self.assertTrue(r["refreshed"])
        self.assertNotEqual(r["token"], tok)
        # 其他机器还拿着旧令牌，refresh 拿到同一份新登录态
        r2 = pool.refresh(tok)
        self.assertEqual(r2["token"], r["token"])
        self.assertEqual(auth.calls, 2)

    def test_refresh_unknown_token(self):
        pool, _ = make_pool()
        r = pool.refresh("nope")
        self.assertIsNone(r["token"])
        self.assertEqual(r["error"]["kind"], "unknown_token")

    def test_kicked_relogin_single_flight(self):
        auth = Auth(ttl=1800)
        pool, _ = make_pool(auth)
        tok = pool.lease("acc")["token"]
        pool.invalidate(tok)  # 模拟被踢下线
        auth.gate = threading.Event()
        results = []

        def worker():
            results.append(pool.refresh(tok))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        while auth.calls < 2:  # 等领导者进入登录
            time.sleep(0.01)
        time.sleep(0.05)  # 让其余线程进入等待
        auth.gate.set()
        for t in threads:
            t.join()
        # 踢下线后只重登一次：首次 lease + 一次重登
        self.assertEqual(auth.calls, 2)
        self.assertEqual(len({r["token"] for r in results}), 1)
        self.assertTrue(all(r["relogin"] for r in results))

    def test_lease_after_kick_relogins_once(self):
        auth = Auth()
        pool, _ = make_pool(auth)
        tok = pool.lease("acc")["token"]
        pool.invalidate(tok)
        r = pool.lease("acc")
        self.assertTrue(r["relogin"])
        self.assertNotEqual(r["token"], tok)
        self.assertEqual(auth.calls, 2)


class PauseTest(unittest.TestCase):
    def test_account_failure_pauses_without_retry(self):
        auth = Auth(error=LoginError(ACCOUNT, "密码错误"))
        pool, clock = make_pool(auth, pause_seconds=60)
        r = pool.lease("acc")
        self.assertIsNone(r["token"])
        self.assertEqual(r["error"]["kind"], ACCOUNT)
        self.assertEqual(r["paused_until"], 1000.0 + 60)
        clock.advance(30)
        r2 = pool.lease("acc")
        self.assertIsNone(r2["token"])
        self.assertEqual(auth.calls, 1)  # 暂停期间不再发起登录

    def test_site_failure_classified_with_short_pause(self):
        auth = Auth(error=LoginError(SITE, "网关 502"))
        pool, _ = make_pool(auth, site_pause_seconds=10)
        r = pool.lease("acc")
        self.assertEqual(r["error"]["kind"], SITE)
        self.assertEqual(r["paused_until"], 1000.0 + 10)

    def test_pause_escalates_on_repeat_failure(self):
        auth = Auth(error=LoginError(ACCOUNT, "被封"))
        pool, clock = make_pool(auth, pause_seconds=60, pause_max=180)
        pool.lease("acc")
        clock.advance(61)
        r = pool.lease("acc")  # 试探仍失败，暂停翻倍
        self.assertEqual(r["paused_until"], 1000.0 + 61 + 120)


class RecoveryTest(unittest.TestCase):
    def test_probe_then_full_recovery(self):
        auth = Auth(error=LoginError(ACCOUNT, "被封"))
        pool, clock = make_pool(auth, pause_seconds=60, probe_successes=2)
        r = pool.lease("acc")
        self.assertIsNone(r["token"])
        clock.advance(61)
        auth.error = None  # 账号恢复
        r = pool.lease("acc")  # 小流量试探：第一次成功
        self.assertTrue(r["probing"])
        self.assertIsNotNone(r["token"])
        r = pool.lease("acc")  # 连续第二次成功，自动恢复接单
        self.assertFalse(r["probing"])
        self.assertIsNone(r["paused_until"])
        self.assertEqual(auth.calls, 2)

    def test_probe_failure_repauses(self):
        auth = Auth(error=LoginError(ACCOUNT, "被封"))
        pool, clock = make_pool(auth, pause_seconds=60)
        pool.lease("acc")
        clock.advance(61)
        r = pool.lease("acc")  # 试探仍失败，回到暂停
        self.assertIsNone(r["token"])
        self.assertGreater(r["paused_until"], clock.t)
        self.assertEqual(auth.calls, 2)


class BackgroundRefreshTest(unittest.TestCase):
    def test_sweep_refreshes_before_expiry(self):
        auth = Auth(ttl=100)
        pool, clock = make_pool(auth, refresh_margin=30)
        tok = pool.lease("acc")["token"]
        clock.advance(80)  # 剩余 20s，进入续期窗口
        pool._sweep()
        self.assertNotEqual(pool.lease("acc")["token"], tok)
        self.assertEqual(auth.calls, 2)

    def test_sweep_skips_paused_account(self):
        auth = Auth(error=LoginError(ACCOUNT, "x"))
        pool, _ = make_pool(auth, pause_seconds=60)
        pool.lease("acc")
        pool._sweep()
        self.assertEqual(auth.calls, 1)

    def test_background_thread_start_stop(self):
        pool = LoginPool(Auth(), refresh_margin=0.05)
        pool.lease("acc")
        t = pool.start_background_refresh(interval=0.05)
        pool.stop_background_refresh()
        self.assertFalse(t.is_alive())


if __name__ == "__main__":
    unittest.main()
