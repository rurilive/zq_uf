"""loginkeep 行为测试。

用假时钟驱动，全部确定性执行、无真实网络、无需等待真实退避时间。
"""

import os
import sys
import tempfile
import threading
import time
import unittest

from loginkeep import (
    AccountError,
    DummyBackend,
    InMemoryStore,
    KickedError,
    LoginManager,
    SiteError,
    HEALTHY,
    PAUSED,
    TRIAL,
    SqliteStore,
    lease,
    refresh,
    reset_default_manager,
)


class FakeClock:
    def __init__(self, start=1000.0):
        self.t = start
        self.sleeps = 0

    def time(self):
        return self.t

    def sleep(self, s):
        # 假时钟：sleep 仅推进虚拟时间，测试跑得快
        self.t += s
        self.sleeps += 1

    def advance(self, s):
        self.t += s


def make_manager(backend=None, store=None, clock=None, **kwargs):
    kwargs.setdefault("start_background", False)
    kwargs.setdefault("renew_margin", 300.0)
    return LoginManager(
        backend=backend or DummyBackend(clock=clock),
        store=store if store is not None else InMemoryStore(),
        clock=clock or FakeClock(),
        **kwargs,
    )


class LeaseReuseTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.backend = DummyBackend(clock=self.clock, default_ttl=3600)
        self.mgr = make_manager(self.backend, clock=self.clock)

    def test_same_account_shares_one_token(self):
        a = self.mgr.lease("acc-1")
        b = self.mgr.lease("acc-1")
        self.assertTrue(a["shared"])
        self.assertEqual(a["token"], b["token"])
        # 两次租约只产生一次真实登录
        self.assertEqual(self.backend.calls.count(("login", "acc-1")), 1)

    def test_different_accounts_get_different_tokens(self):
        a = self.mgr.lease("acc-1")
        b = self.mgr.lease("acc-2")
        self.assertNotEqual(a["token"], b["token"])

    def test_lease_reports_remaining_ttl(self):
        # 第一次登录成功后，后续不再发生登录/续期，给空脚本即可
        backend = DummyBackend(script=[3600], clock=self.clock)
        mgr = make_manager(backend, clock=self.clock)
        view = mgr.lease("acc-1")
        self.assertEqual(view["expires_in"], 3600.0)
        self.clock.advance(600)
        view = mgr.lease("acc-1")
        self.assertEqual(view["expires_in"], 3000.0)
        self.assertFalse(view["ever_refreshed"])

    def test_expires_at_zero_ttl_when_token_dies(self):
        self.mgr.lease("acc-1")
        self.clock.advance(3601)
        # 过期后租约触发单飞重登，返回新令牌
        view = self.mgr.lease("acc-1")
        self.assertTrue(view["ok"])
        self.assertTrue(view["ever_relogin"])
        self.assertEqual(view["expires_in"], 3600.0)


class ProactiveRenewTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.backend = DummyBackend(clock=self.clock, default_ttl=1000)
        self.mgr = make_manager(
            self.backend, clock=self.clock, renew_margin=300
        )

    def test_renew_before_expiry_in_lease(self):
        first = self.mgr.lease("acc-1")
        self.clock.advance(750)  # 剩余 250s，进入续期窗口
        view = self.mgr.lease("acc-1")
        # 走的是静默续期而不是重登
        self.assertTrue(view["ever_refreshed"])
        self.assertFalse(view["ever_relogin"])
        self.assertEqual(view["token"], first["token"])
        self.assertEqual(view["expires_in"], 1000.0)
        self.assertTrue(
            any(c[0] == "renew" for c in self.backend.calls)
        )

    def test_refresh_api_renews_token(self):
        self.mgr.lease("acc-1")
        out = self.mgr.refresh(self.mgr.store.get("acc-1").token)
        self.assertTrue(out["ok"])
        self.assertTrue(out["refreshed"])
        self.assertFalse(out["relogin"])

    def test_background_tick_renews_without_traffic(self):
        self.mgr.lease("acc-1")
        self.clock.advance(750)
        self.mgr.tick()  # 后台周期任务
        rec = self.mgr.store.get("acc-1")
        self.assertTrue(rec.ever_refreshed)
        self.assertEqual(rec.token_expires_at - self.clock.time(), 1000.0)

    def test_non_renewable_token_skips_to_relogin(self):
        backend = DummyBackend(clock=self.clock, default_ttl=1000, renewable=False)
        mgr = make_manager(backend, clock=self.clock, renew_margin=300)
        mgr.lease("acc-1")
        self.clock.advance(750)
        view = mgr.lease("acc-1")
        # 不支持续期：窗口内不 renew，过期后直接重登
        renew_calls = [c for c in backend.calls if c[0] == "renew"]
        self.assertEqual(renew_calls, [])
        self.clock.advance(300)
        view = mgr.lease("acc-1")
        self.assertTrue(view["ever_relogin"])


class KickedReloginTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        # 调用顺序：登录成功 -> 续期报被踢 -> 重登成功
        self.backend = DummyBackend(
            script=[1000, KickedError("kicked"), 1000],
            clock=self.clock,
            default_ttl=1000,
        )
        self.mgr = make_manager(self.backend, clock=self.clock)

    def test_kicked_triggers_single_relogin(self):
        self.mgr.lease("acc-1")
        out = self.mgr.refresh(self.mgr.store.get("acc-1").token)
        self.assertTrue(out["ok"])
        self.assertTrue(out["relogin"])
        self.assertTrue(out["ever_relogin"])
        logins = [c for c in self.backend.calls if c[0] == "login"]
        self.assertEqual(len(logins), 2)  # 首次 + 被踢后唯一一次重登

    def test_kicked_relogin_rotates_token(self):
        first = self.mgr.lease("acc-1")
        out = self.mgr.refresh(first["token"])
        self.assertNotEqual(out["token"], first["token"])
        # 旧令牌仍能反查到账号（刷新旧令牌不报错）
        self.assertEqual(self.mgr.store.lookup_token(first["token"]), "acc-1")


class FailureClassificationTest(unittest.TestCase):
    def test_account_error_pauses_account_with_long_backoff(self):
        clock = FakeClock()
        backend = DummyBackend(script=[AccountError("banned")], clock=clock)
        mgr = make_manager(
            backend, clock=clock, account_backoff=300, account_backoff_max=3600
        )
        view = mgr.lease("acc-1")
        self.assertFalse(view["ok"])
        self.assertEqual(view["error_kind"], "account")
        self.assertEqual(view["pause_reason"], "banned")
        self.assertEqual(view["state"], PAUSED)
        self.assertEqual(view["retry_after"], 300.0)
        self.assertIsNone(view["token"])
        # 暂停期间不再发起登录
        calls_before = len(backend.calls)
        again = mgr.lease("acc-1")
        self.assertFalse(again["ok"])
        self.assertEqual(len(backend.calls), calls_before)

    def test_site_error_pauses_with_short_backoff(self):
        clock = FakeClock()
        backend = DummyBackend(script=[SiteError("bad_gateway")], clock=clock)
        mgr = make_manager(backend, clock=clock, site_backoff=30, site_backoff_max=300)
        view = mgr.lease("acc-1")
        self.assertEqual(view["error_kind"], "site")
        self.assertEqual(view["retry_after"], 30.0)

    def test_backoff_grows_exponentially(self):
        clock = FakeClock()
        backend = DummyBackend(
            script=[
                AccountError(),
                AccountError(),
                AccountError(),
            ],
            clock=clock,
        )
        mgr = make_manager(
            backend, clock=clock, account_backoff=100, account_backoff_max=10000
        )
        v1 = mgr.lease("a")
        self.assertEqual(v1["retry_after"], 100.0)
        clock.advance(v1["retry_after"] + 1)
        v2 = mgr.lease("a")  # 试探登录再次失败
        self.assertEqual(v2["retry_after"], 200.0)
        clock.advance(v2["retry_after"] + 1)
        v3 = mgr.lease("a")
        self.assertEqual(v3["retry_after"], 400.0)

    def test_other_account_unaffected(self):
        clock = FakeClock()
        backend = DummyBackend(script=[AccountError(), 1000], clock=clock)
        mgr = make_manager(backend, clock=clock)
        bad = mgr.lease("bad-acc")
        self.assertFalse(bad["ok"])
        good = mgr.lease("good-acc")
        self.assertTrue(good["ok"])


class AutoRecoveryTrialTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        # 首次登录失败 -> 暂停；之后全部成功
        self.backend = DummyBackend(
            script=[
                AccountError("risk_control"),
                1000, 1000, 1000, 1000, 1000, 1000,
            ],
            clock=self.clock,
            default_ttl=1000,
        )
        self.mgr = make_manager(
            self.backend,
            clock=self.clock,
            account_backoff=300,
            trial_success_needed=3,
        )

    def test_pause_then_trial_then_full_recovery(self):
        v = self.mgr.lease("acc-1")
        self.assertEqual(v["state"], PAUSED)

        # 暂停未结束：不接单、不登录
        self.clock.advance(100)
        v = self.mgr.lease("acc-1")
        self.assertEqual(v["state"], PAUSED)

        # 暂停结束：自动进入 TRIAL 小流量试探，无需重启调度；
        # 恢复登录本身算第 1 次试探成功
        self.clock.advance(201)
        v = self.mgr.lease("acc-1")
        self.assertEqual(v["state"], TRIAL)
        self.assertEqual(v["rate"], "trial")

        # 再积累成功（静默续期），达标前始终保持试探
        self.clock.advance(750)  # 进入续期窗口 -> 第 2 次成功，仍未达标
        v = self.mgr.lease("acc-1")
        self.assertEqual(v["state"], TRIAL)
        self.clock.advance(1)
        v = self.mgr.lease("acc-1")
        self.assertEqual(v["state"], TRIAL)  # 复用令牌，不计成功
        self.clock.advance(749)  # 再次续期 -> 第 3 次成功，恢复满速
        v = self.mgr.lease("acc-1")
        self.assertEqual(v["state"], HEALTHY)
        self.assertEqual(v["rate"], "full")
        self.assertEqual(v["fail_count"], 0)

    def test_trial_failure_repauses(self):
        # 序列：首次登录失败 -> 试探成功 -> 再次登录失败
        backend = DummyBackend(
            script=[AccountError(), 1000, AccountError()],
            clock=self.clock,
        )
        mgr = make_manager(backend, clock=self.clock, account_backoff=100)
        mgr.lease("a")  # 第 1 次失败，fail_count=1，暂停
        self.clock.advance(101)
        v = mgr.lease("a")  # 暂停结束，试探登录成功（第 2 条脚本）
        self.assertEqual(v["state"], TRIAL)
        # 令牌过期后再租约：触发重新登录，命中第 3 条失败脚本
        self.clock.advance(2000)
        v = mgr.lease("a")
        self.assertEqual(v["state"], PAUSED)
        self.assertEqual(v["fail_count"], 2)

    def test_tick_auto_resumes_paused_account(self):
        self.mgr.lease("acc-1")
        self.clock.advance(301)
        self.mgr.tick()  # 后台扫描：暂停到期自动转 TRIAL 并试探登录
        rec = self.mgr.store.get("acc-1")
        self.assertEqual(rec.state, TRIAL)
        self.assertIsNotNone(rec.token)


class SingleFlightTest(unittest.TestCase):
    """并发/跨进程单飞：同一账号同时只允许一次真实登录。"""

    def test_concurrent_leases_in_one_process_login_once(self):
        clock = FakeClock()

        class SlowBackend(DummyBackend):
            def __init__(self):
                super().__init__(clock=clock, default_ttl=1000)
                self.login_lock = threading.Lock()
                self.logins = 0

            def login(self, account):
                with self.login_lock:
                    self.logins += 1
                time.sleep(0.05)  # 放大竞争窗口
                return super().login(account)

        backend = SlowBackend()
        mgr = LoginManager(
            backend=backend,
            store=InMemoryStore(),
            clock=clock,
            start_background=False,
            poll_interval=0.01,
        )
        results = []

        def worker():
            results.append(mgr.lease("hot-acc"))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(backend.logins, 1)
        tokens = {r["token"] for r in results}
        self.assertEqual(len(tokens), 1)
        self.assertTrue(all(r["shared"] for r in results))

    def test_two_managers_share_sqlite_session(self):
        # 两个管理器（模拟两台机器）挂同一个 SQLite 文件
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            clock = FakeClock()

            class CountingBackend(DummyBackend):
                def __init__(self):
                    super().__init__(clock=clock, default_ttl=1000)
                    self.logins = 0
                    self.gate = threading.Event()

                def login(self, account):
                    self.logins += 1
                    self.gate.wait(2.0)
                    return super().login(account)

            backend = CountingBackend()
            store1 = SqliteStore(path)
            store2 = SqliteStore(path)
            m1 = LoginManager(
                backend=backend, store=store1, clock=clock,
                start_background=False, poll_interval=0.01,
            )
            m2 = LoginManager(
                backend=backend, store=store2, clock=clock,
                start_background=False, poll_interval=0.01,
            )
            out = {}

            def first():
                out["a"] = m1.lease("shared-acc")

            th = threading.Thread(target=first)
            th.start()
            time.sleep(0.05)  # 确保 m1 先拿锁并卡在登录里
            backend.gate.set()
            v2 = m2.lease("shared-acc")
            th.join()

            self.assertEqual(backend.logins, 1)
            self.assertEqual(out["a"]["token"], v2["token"])
            self.assertTrue(v2["shared"])
            store1.close()
            store2.close()
        finally:
            for p in (path, path + "-wal", path + "-shm"):
                if os.path.exists(p):
                    os.unlink(p)

    def test_login_lock_ttl_allows_takeover(self):
        clock = FakeClock()
        store = InMemoryStore()
        self.assertTrue(store.try_acquire_login("a", "dead-owner", 60, clock.time()))
        self.assertFalse(store.try_acquire_login("a", "new-owner", 60, clock.time()))
        clock.advance(61)
        self.assertTrue(store.try_acquire_login("a", "new-owner", 60, clock.time()))

    def test_real_multiprocess_single_flight(self):
        """4 个独立进程挂同一个 SQLite 文件并发 lease，全局只登录一次。"""
        import json
        import subprocess
        import tempfile

        workdir = tempfile.mkdtemp(prefix="lk-mp-")
        db_path = os.path.join(workdir, "sessions.db")
        counter = os.path.join(workdir, "login.count")
        result_dir = os.path.join(workdir, "results")
        os.makedirs(result_dir)
        worker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_mp_worker.py")

        procs = []
        for i in range(4):
            env = dict(os.environ)
            env.update({
                "LK_DB_PATH": db_path,
                "LK_COUNTER_PATH": counter,
                "LK_RESULT_DIR": result_dir,
                "LK_WORKER_ID": str(i),
            })
            procs.append(subprocess.Popen(
                [sys.executable, "-I", worker],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ))
        for p in procs:
            out, err = p.communicate(timeout=30)
            self.assertEqual(p.returncode, 0, err.decode())

        # 全局真实登录次数：1
        self.assertTrue(os.path.exists(counter))
        with open(counter) as f:
            self.assertEqual(f.read(), "1")

        # 四个进程拿到完全相同的令牌
        tokens = set()
        for i in range(4):
            with open(os.path.join(result_dir, "worker-%d.json" % i)) as f:
                payload = json.load(f)
            self.assertTrue(payload["ok"])
            tokens.add(payload["token"])
        self.assertEqual(tokens, {"tok-multi-acc"})


class UnknownTokenTest(unittest.TestCase):
    def test_refresh_unknown_token_is_structured_error(self):
        mgr = make_manager()
        out = mgr.refresh("nobody-owns-this")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_kind"], "unknown_token")
        self.assertFalse(out["refreshed"])
        self.assertFalse(out["relogin"])


class ModuleLevelApiTest(unittest.TestCase):
    def tearDown(self):
        reset_default_manager()

    def test_module_level_lease_and_refresh(self):
        clock = FakeClock()
        from loginkeep import configure

        configure(
            backend=DummyBackend(clock=clock, default_ttl=1000),
            store=InMemoryStore(),
            clock=clock,
            start_background=False,
        )
        v1 = lease("acc-x")
        v2 = lease("acc-x")
        self.assertEqual(v1["token"], v2["token"])
        r = refresh(v1["token"])
        self.assertTrue(r["ok"])
        self.assertTrue(r["refreshed"])


if __name__ == "__main__":
    unittest.main()
