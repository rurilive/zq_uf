import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path

import loginkeep
from loginkeep import FileStore, MemoryStore, SessionPool
from loginkeep.simulator import SimulatedClock, SimulatedSite

REPO = Path(__file__).resolve().parent.parent


def make_pool(ttl=600.0, **options):
    clock = SimulatedClock()
    site = SimulatedSite(ttl=ttl)
    pool = SessionPool(site, clock=clock, background=False, **options)
    return pool, site, clock


class SharedLeaseTest(unittest.TestCase):
    def test_same_account_reuses_token(self):
        pool, site, _ = make_pool()
        first = pool.lease("a")
        second = pool.lease("a")
        self.assertEqual(first["token"], second["token"])
        self.assertEqual(len(site.login_calls), 1)
        self.assertTrue(second["shared"])
        self.assertGreater(second["expires_in"], 0)

    def test_different_accounts_do_not_share(self):
        pool, _, _ = make_pool()
        self.assertNotEqual(pool.lease("a")["token"], pool.lease("b")["token"])

    def test_two_pools_share_one_store(self):
        store = MemoryStore()
        clock = SimulatedClock()
        site = SimulatedSite()
        machine_a = SessionPool(site, clock=clock, store=store, background=False)
        machine_b = SessionPool(site, clock=clock, store=store, background=False)
        token_a = machine_a.lease("acc")["token"]
        token_b = machine_b.lease("acc")["token"]
        self.assertEqual(token_a, token_b)
        self.assertEqual(len(site.login_calls), 1)

    def test_status_does_not_login(self):
        pool, site, _ = make_pool()
        pool.lease("a")
        calls = len(site.login_calls)
        pool.status("a")
        self.assertEqual(len(site.login_calls), calls)


class RefreshTest(unittest.TestCase):
    def test_refresh_before_expiry_is_background(self):
        pool, site, clock = make_pool(ttl=100.0)
        token = pool.lease("a")["token"]
        # TTL 的 25% 作为提前量：75 秒后后台 tick 应已续期。
        clock.advance(80)
        pool.tick()
        self.assertEqual(len(site.refresh_calls), 1)
        refreshed = pool.lease("a")
        self.assertNotEqual(refreshed["token"], token)
        self.assertEqual(refreshed["token_via"], "refresh")
        self.assertEqual(refreshed["refresh_count"], 1)
        self.assertGreater(refreshed["expires_in"], 90)
        self.assertEqual(len(site.login_calls), 1)

    def test_lease_still_works_without_background(self):
        pool, site, clock = make_pool(ttl=100.0)
        pool.lease("a")
        clock.advance(80)  # 后台没跑，前台兜底续期
        result = pool.lease("a")
        self.assertTrue(result["refreshed"])
        self.assertEqual(len(site.login_calls), 1)

    def test_refresh_by_token(self):
        pool, site, clock = make_pool(ttl=100.0)
        token = pool.lease("a")["token"]
        clock.advance(80)
        result = pool.refresh(token)
        self.assertTrue(result["refreshed"])
        self.assertFalse(result["relogin"])
        self.assertGreater(result["expires_in"], 90)

    def test_refresh_with_plenty_of_time_still_validates(self):
        # 显式 refresh 是为了验证令牌是否还有效，即使离过期还早也要问站点。
        pool, site, _ = make_pool(ttl=100.0)
        token = pool.lease("a")["token"]
        result = pool.refresh(token)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(site.refresh_calls), 1)

    def test_unknown_token_is_reported(self):
        pool, _, _ = make_pool()
        result = pool.refresh("nope")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["error"]["kind"], "unknown")

    def test_plain_expiry_is_not_a_relogin(self):
        pool, site, clock = make_pool(ttl=100.0)
        pool.lease("a")
        clock.advance(101)
        result = pool.lease("a")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["relogin"])
        self.assertEqual(result["relogin_count"], 0)
        self.assertEqual(result["token_via"], "login")
        self.assertEqual(len(site.login_calls), 2)

    def test_soft_refresh_failure_keeps_old_token(self):
        pool, site, clock = make_pool(ttl=100.0)
        token = pool.lease("a")["token"]
        site.fail_next("a", kind="site", count=1)
        clock.advance(80)  # 距离过期还有 20 秒，仍在可用范围内
        result = pool.lease("a")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["token"], token)
        self.assertIsNone(result["paused_until"])
        self.assertIn("refresh_error", result)


class KickedTest(unittest.TestCase):
    def test_kick_causes_single_relogin(self):
        pool, site, _ = make_pool()
        old = pool.lease("a")["token"]
        site.kick("a")
        result = pool.refresh(old)
        self.assertTrue(result["relogin"])
        self.assertEqual(len(site.login_calls), 2)
        self.assertEqual(result["relogin_count"], 1)

    def test_repeated_refresh_of_kicked_token_does_not_relogin_again(self):
        pool, site, _ = make_pool()
        old = pool.lease("a")["token"]
        site.kick("a")
        first = pool.refresh(old)
        second = pool.refresh(old)
        self.assertEqual(first["token"], second["token"])
        self.assertTrue(second["deduped"])
        self.assertEqual(len(site.login_calls), 2)

    def test_concurrent_kicked_requests_relogin_once(self):
        pool, site, _ = make_pool()
        old = pool.lease("a")["token"]
        site.kick("a")
        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(pool.refresh(old))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(site.login_calls), 2)  # 首次 + 一次重登
        self.assertEqual(len({r["token"] for r in results}), 1)

    def test_kick_loop_stops_after_max_relogins(self):
        pool, site, _ = make_pool(
            ttl=100.0, relogin_max=2, relogin_window=600.0, site_pause=30.0
        )
        token = pool.lease("a")["token"]
        for _ in range(2):
            site.kick("a")
            token = pool.refresh(token)["token"]
        site.kick("a")
        result = pool.refresh(token)
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["error"]["kind"], "site")


class FailureClassificationTest(unittest.TestCase):
    def test_account_failure_pauses_account(self):
        pool, site, clock = make_pool(account_pause=1800.0)
        site.fail_next("a", kind="account")
        result = pool.lease("a")
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["error"]["kind"], "account")
        self.assertEqual(result["paused_until"], clock.now() + 1800)
        self.assertEqual(result["retry_after"], 1800)

    def test_site_failure_uses_short_pause(self):
        pool, site, _ = make_pool(account_pause=1800.0, site_pause=30.0)
        site.fail_next("a", kind="site")
        result = pool.lease("a")
        self.assertEqual(result["error"]["kind"], "site")
        self.assertEqual(result["retry_after"], 30)

    def test_unexpected_exception_is_site_side(self):
        class Broken:
            def login(self, account):
                raise RuntimeError("socket reset")

            def refresh(self, token):
                raise RuntimeError("socket reset")

        pool = SessionPool(Broken(), clock=SimulatedClock(), background=False)
        result = pool.lease("a")
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["error"]["kind"], "site")

    def test_pause_backs_off_on_repeated_failures(self):
        pool, site, clock = make_pool(account_pause=10.0, account_pause_max=40.0)
        pauses = []
        for _ in range(3):
            site.fail_next("a", kind="account")
            pauses.append(pool.lease("a")["retry_after"])
            clock.advance(pauses[-1] + 1)
        self.assertEqual(pauses, [10.0, 20.0, 40.0])

    def test_no_login_during_pause(self):
        pool, site, clock = make_pool(account_pause=100.0)
        site.fail_next("a", kind="account")
        pool.lease("a")
        calls = len(site.login_calls)
        for _ in range(5):
            self.assertEqual(pool.lease("a")["status"], "paused")
        self.assertEqual(len(site.login_calls), calls)
        clock.advance(101)
        pool.lease("a")
        self.assertEqual(len(site.login_calls), calls + 1)


class ProbationTest(unittest.TestCase):
    def test_status_is_read_only_after_pause(self):
        pool, site, clock = make_pool(account_pause=10.0)
        site.fail_next("a", kind="account")
        pool.lease("a")
        clock.advance(11)
        snap = pool.status("a")
        self.assertEqual(snap["status"], "idle")
        self.assertTrue(snap["probation"])
        self.assertEqual(snap["rate"]["max_inflight"], 1)
        # status 不应消耗掉试探期：随后 lease 仍需真正试探登录一次。
        self.assertEqual(pool.lease("a")["status"], "ok")
        self.assertEqual(len(site.login_calls), 2)

    def test_tick_auto_resumes_after_pause(self):
        pool, site, clock = make_pool(
            account_pause=100.0, probation_successes=2, normal_inflight=8
        )
        site.fail_next("a", kind="account")
        self.assertEqual(pool.lease("a")["status"], "paused")

        clock.advance(101)
        pool.tick()  # 无需人工调用 lease
        self.assertEqual(len(site.login_calls), 2)
        snap = pool.status("a")
        self.assertEqual(snap["status"], "ok")
        self.assertTrue(snap["probation"])
        self.assertEqual(snap["rate"]["max_inflight"], 1)

        clock.advance(1)
        pool.lease("a")
        final = pool.status("a")
        self.assertFalse(final["probation"])
        self.assertEqual(final["rate"]["max_inflight"], 8)

    def test_tick_does_not_login_during_pause(self):
        pool, site, clock = make_pool(account_pause=100.0)
        site.fail_next("a", kind="account")
        pool.lease("a")
        for _ in range(5):
            clock.advance(1)
            pool.tick()
        self.assertEqual(len(site.login_calls), 1)

    def test_resume_then_ramp_up(self):
        pool, site, clock = make_pool(
            account_pause=100.0, probation_successes=3, normal_inflight=8
        )
        site.fail_next("a", kind="account")
        assert pool.lease("a")["status"] == "paused"

        clock.advance(101)
        first = pool.lease("a")
        self.assertEqual(first["status"], "ok")
        self.assertTrue(first["probation"])
        self.assertEqual(first["rate"]["max_inflight"], 1)

        clock.advance(1)
        second = pool.lease("a")
        self.assertTrue(second["probation"])
        clock.advance(1)
        third = pool.lease("a")
        self.assertFalse(third["probation"])
        self.assertEqual(third["rate"]["max_inflight"], 8)
        self.assertEqual(len(site.login_calls), 2)

    def test_probation_failure_pauses_again(self):
        pool, site, clock = make_pool(account_pause=50.0)
        site.fail_next("a", kind="account")
        pool.lease("a")
        clock.advance(51)
        self.assertTrue(pool.lease("a")["probation"])
        site.fail_next("a", kind="account")
        clock.advance(601)  # 令牌过期，需要重新登录
        result = pool.lease("a")
        self.assertEqual(result["status"], "paused")
        self.assertFalse(result["probation"])

    def test_status_reports_probation_without_login(self):
        pool, site, clock = make_pool(account_pause=10.0)
        site.fail_next("a", kind="account")
        pool.lease("a")
        clock.advance(11)
        snap = pool.status("a")
        self.assertEqual(snap["status"], "idle")
        self.assertTrue(snap["probation"])
        self.assertEqual(snap["rate"]["max_inflight"], 1)


class FileStoreTest(unittest.TestCase):
    def test_state_persists_across_pool_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = SimulatedSite()
            clock = SimulatedClock()
            pool_a = SessionPool(
                site, clock=clock, store=FileStore(tmp), background=False
            )
            token = pool_a.lease("acc")["token"]
            pool_b = SessionPool(
                site, clock=clock, store=FileStore(tmp), background=False
            )
            self.assertEqual(pool_b.lease("acc")["token"], token)
            self.assertEqual(len(site.login_calls), 1)

    def test_pause_is_visible_to_other_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = SimulatedSite()
            clock = SimulatedClock()
            pool_a = SessionPool(
                site, clock=clock, store=FileStore(tmp), background=False
            )
            pool_b = SessionPool(
                site, clock=clock, store=FileStore(tmp), background=False
            )
            site.fail_next("acc", kind="account")
            pool_a.lease("acc")
            result = pool_b.lease("acc")
            self.assertEqual(result["status"], "paused")
            self.assertEqual(result["error"]["kind"], "account")
            self.assertEqual(len(site.login_calls), 1)

    def test_corrupt_state_file_is_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "state.json").write_text("{not json", encoding="utf-8")
            pool = SessionPool(
                SimulatedSite(), clock=SimulatedClock(),
                store=FileStore(tmp), background=False,
            )
            self.assertEqual(pool.lease("acc")["status"], "ok")

    def test_unexpected_json_shape_is_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "state.json").write_text("[1, 2, 3]", encoding="utf-8")
            pool = SessionPool(
                SimulatedSite(), clock=SimulatedClock(),
                store=FileStore(tmp), background=False,
            )
            self.assertEqual(pool.lease("acc")["status"], "ok")
            state = Path(tmp, "state.json").read_text(encoding="utf-8")
            self.assertIn("acc", state)


PROCESS_SCRIPT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from loginkeep import FileStore, SessionPool
    from loginkeep.simulator import SimulatedClock, SimulatedSite

    class CountingSite(SimulatedSite):
        def login(self, account):
            print("LOGIN", account)
            return super().login(account)

    pool = SessionPool(
        CountingSite(), clock=SimulatedClock(),
        store=FileStore(sys.argv[2]), background=False,
    )
    print(pool.lease(sys.argv[3])["token"])
    """
)


class MultiProcessTest(unittest.TestCase):
    def test_two_processes_share_one_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = []
            for _ in range(2):
                proc = subprocess.run(
                    [sys.executable, "-c", PROCESS_SCRIPT, str(REPO), tmp, "acc"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                outputs.append(proc.stdout)
            tokens = [
                line for out in outputs for line in out.splitlines()
                if line.startswith("tok-")
            ]
            logins = [
                line for out in outputs for line in out.splitlines()
                if line.startswith("LOGIN")
            ]
            self.assertEqual(len(set(tokens)), 1)
            self.assertEqual(len(logins), 1)


class BackgroundTest(unittest.TestCase):
    def test_background_thread_refreshes_without_leases(self):
        import time

        clock = SimulatedClock()
        site = SimulatedSite(ttl=100.0)
        pool = SessionPool(
            site,
            clock=clock,
            background=True,
            background_interval=0.01,
        )
        try:
            pool.lease("a")
            clock.advance(80)
            deadline = time.time() + 3.0
            while not site.refresh_calls and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(site.refresh_calls), 1)
            self.assertEqual(pool.lease("a")["refresh_count"], 1)
        finally:
            pool.stop_background()

    def test_stop_background_is_idempotent(self):
        pool = SessionPool(
            SimulatedSite(), clock=SimulatedClock(), background=True,
            background_interval=0.01,
        )
        pool.stop_background()
        pool.stop_background()
        self.assertIsNone(pool._thread)


class ModuleApiTest(unittest.TestCase):
    def test_module_level_lease_and_refresh(self):
        site = SimulatedSite()
        clock = SimulatedClock()
        loginkeep.configure(site=site, clock=clock, background=False)
        try:
            first = loginkeep.lease("m")
            second = loginkeep.lease("m")
            self.assertEqual(first["token"], second["token"])
            refreshed = loginkeep.refresh(first["token"])
            self.assertEqual(refreshed["status"], "ok")
        finally:
            loginkeep.configure(site=SimulatedSite(), background=False)

    def test_lease_requires_configure(self):
        import loginkeep.core as core

        saved = core._default_pool
        core._default_pool = None
        try:
            with self.assertRaises(RuntimeError):
                loginkeep.lease("x")
        finally:
            core._default_pool = saved

    def test_result_carries_required_fields(self):
        pool, _, _ = make_pool()
        result = pool.lease("a")
        for key in (
            "expires_in",
            "refreshed",
            "relogin",
            "paused_until",
            "rate",
            "probation",
        ):
            self.assertIn(key, result)


if __name__ == "__main__":
    unittest.main()
