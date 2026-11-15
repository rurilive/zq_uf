"""审计、过期失效、安全回收与会话状态的补充测试。

旧测试 tests/test_audit.py 的断言保持不变；这里全部通过注入时钟和临时目录的
独立 SessionManager 验证，不依赖真实时间和全局状态。
"""

import json
import os
import shutil
import tempfile
import unittest

from sessionaudit import SessionManager


class FakeClock:
    def __init__(self, start=1000):
        self.now = start

    def __call__(self):
        return self.now


def _noop_sleep(*_args):
    return None


class SessionManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sessionaudit-test-")
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            manager.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_manager(self, **kwargs):
        clock = FakeClock()
        kwargs.setdefault("ttl", 100)
        manager = SessionManager(
            store_dir=self.tmp,
            clock=clock,
            sleep=_noop_sleep,
            **kwargs,
        )
        self.managers.append(manager)
        return manager, clock

    # ---- 审计记录 -------------------------------------------------------

    def test_use_writes_audit_with_account_site_time(self):
        manager, clock = self.make_manager(site="site-a")
        manager.use("acc-1")
        clock.now = 1050
        manager.use("acc-2")
        manager.use("acc-1")

        records = manager.query_audit(account="acc-1")
        self.assertEqual([r["account"] for r in records], ["acc-1", "acc-1"])
        self.assertTrue(all(r["site"] == "site-a" for r in records))
        self.assertEqual(records[1]["time"], 1050)
        self.assertEqual(len(manager.query_audit(site="site-a")), 3)
        self.assertEqual(manager.query_audit(site="other"), [])
        self.assertEqual(len(manager.query_audit(event="use")), 3)

        # 审计落盘为 JSON Lines，每行一条完整记录
        with open(os.path.join(self.tmp, "audit.log"), encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(json.loads(line)["account"] for line in lines))

    def test_audit_log_shared_across_sites_and_queryable_per_site(self):
        manager_a, clock = self.make_manager(site="site-a")
        manager_a.use("acc-1")
        manager_b = SessionManager(
            store_dir=self.tmp, site="site-b", clock=clock,
            sleep=_noop_sleep, ttl=100,
        )
        self.managers.append(manager_b)
        manager_b.use("acc-1")

        self.assertEqual(len(manager_a.query_audit(site="site-a")), 1)
        self.assertEqual(len(manager_b.query_audit(site="site-b")), 1)
        self.assertEqual(len(manager_b.query_audit(account="acc-1")), 2)

    def test_use_response_keeps_audit_empty_but_log_has_record(self):
        manager, _clock = self.make_manager()
        result = manager.use("acc-9")
        self.assertEqual(result["audit"], [])
        self.assertTrue(result["session"].startswith("s-acc-9-"))
        self.assertEqual(len(manager.query_audit(account="acc-9")), 1)

    # ---- 过期失效（统一时钟、滑动 TTL） ---------------------------------

    def test_expired_session_becomes_unusable_and_is_replaced(self):
        manager, clock = self.make_manager(ttl=100)
        first = manager.use("acc-1")
        clock.now = 1050  # 未过期：复用同一会话
        second = manager.use("acc-1")
        self.assertEqual(first["session"], second["session"])

        clock.now = 1200  # 最后使用 1050 + ttl 100 <= 1200：已过期
        third = manager.use("acc-1")
        self.assertNotEqual(second["session"], third["session"])

        states = {s["session"]: s["status"]
                  for s in manager.export_state()["sessions"]}
        self.assertEqual(states[second["session"]], "expired")
        self.assertEqual(states[third["session"]], "active")
        expire_events = manager.query_audit(event="expire")
        self.assertEqual(len(expire_events), 1)

    def test_sweep_expires_and_reclaims_session_files(self):
        manager, clock = self.make_manager(ttl=100)
        manager.use("acc-1")
        sid = manager.use("acc-1")["session"]
        path = os.path.join(self.tmp, f"{sid}.json")
        self.assertTrue(os.path.exists(path))

        before = manager.sweep(1099)  # 边界之前：仍有效
        self.assertEqual(before["expired"], [])
        self.assertEqual(before["reclaimed"], [])

        result = manager.sweep(1100)  # last_used 1000 + ttl 100 <= 1100
        self.assertEqual(result["now"], 1100)
        self.assertEqual(result["expired"], [sid])
        self.assertEqual(result["reclaimed"], [sid])
        self.assertFalse(os.path.exists(path))
        self.assertEqual(manager.export_state()["sessions"][0]["status"],
                         "reclaimed")
        # 第二轮没有残留可回收
        self.assertEqual(manager.sweep(1200)["reclaimed"], [])

    def test_unified_clock_drives_expiry_and_audit_timestamps(self):
        manager, clock = self.make_manager(ttl=100)
        manager.use("acc-1")  # 时间固定在 1000，真实时间不走动
        self.assertEqual(manager.query_audit()[0]["time"], 1000)
        self.assertEqual(manager.sweep(1099)["expired"], [])
        self.assertEqual(manager.sweep(1100)["expired"], ["s-acc-1-1"])
        self.assertEqual(
            [r["time"] for r in manager.query_audit(event="expire")],
            [1100],
        )

    # ---- 安全回收：在跑任务保护、句柄释放、失败重试 ----------------------

    def test_busy_session_is_skipped_until_task_ends(self):
        manager, clock = self.make_manager(ttl=100)
        with manager.task("acc-1") as session:
            clock.now = 1200
            result = manager.sweep(1200)
            self.assertEqual(result["expired"], [session.session_id])
            self.assertEqual(result["skipped"], [session.session_id])
            self.assertEqual(result["reclaimed"], [])
            self.assertTrue(os.path.exists(session.path))

        result = manager.sweep(1200)  # 任务结束后才回收
        self.assertEqual(result["reclaimed"], [session.session_id])
        self.assertFalse(os.path.exists(session.path))

    def test_handle_is_closed_before_file_removal(self):
        manager, _clock = self.make_manager(ttl=100)
        seen = {}

        def fake_remove(path):
            seen["handle_closed"] = manager._all[0].handle.closed
            os.remove(path)

        manager._remove = fake_remove
        manager.use("acc-1")
        manager.sweep(1100)
        self.assertTrue(seen["handle_closed"])

    def test_reclaim_retries_after_transient_failure(self):
        manager, _clock = self.make_manager(ttl=100, retry_attempts=3)
        calls = {"n": 0}
        real_remove = os.remove

        def flaky(path):
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("device busy")
            real_remove(path)

        manager._remove = flaky
        manager.use("acc-1")
        result = manager.sweep(1100)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(result["reclaimed"], ["s-acc-1-1"])

    def test_reclaim_failure_is_reported_and_retried_next_sweep(self):
        manager, _clock = self.make_manager(ttl=100, retry_attempts=2)
        manager._remove = lambda path: (_ for _ in ()).throw(OSError("nope"))
        manager.use("acc-1")

        first = manager.sweep(1100)
        self.assertEqual(first["reclaimed"], [])
        self.assertEqual(first["failed"], ["s-acc-1-1"])
        self.assertTrue(
            os.path.exists(os.path.join(self.tmp, "s-acc-1-1.json"))
        )

        manager._remove = os.remove  # 恢复后下一轮回收成功
        second = manager.sweep(1200)
        self.assertEqual(second["reclaimed"], ["s-acc-1-1"])
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp, "s-acc-1-1.json"))
        )

    # ---- 审计保留期可配置 ----------------------------------------------

    def test_audit_pruned_according_to_retention(self):
        manager, clock = self.make_manager(ttl=100, audit_retention=500)
        manager.use("acc-1")  # t=1000
        clock.now = 1200
        manager.use("acc-2")  # t=1200

        result = manager.sweep(1600)  # 裁剪 time < 1100 的记录
        self.assertEqual(len(result["audit_pruned"]), 1)
        self.assertTrue(all(r["time"] >= 1100 for r in manager.query_audit()))
        self.assertTrue(
            any(r["event"] == "audit_pruned" for r in manager.query_audit())
        )

    def test_audit_kept_forever_when_retention_none(self):
        manager, _clock = self.make_manager(ttl=100, audit_retention=None)
        manager.use("acc-1")
        manager.sweep(10000)
        self.assertTrue(
            any(r["event"] == "use" for r in manager.query_audit())
        )
        self.assertEqual(manager.sweep(10001)["audit_pruned"], [])

    # ---- 会话状态导出 ---------------------------------------------------

    def test_export_state_contains_sessions_and_writes_json_file(self):
        manager, clock = self.make_manager(ttl=100)
        manager.use("acc-1")
        manager.use("acc-2")
        clock.now = 1050
        manager.use("acc-1")

        state = manager.export_state()
        self.assertEqual(state["site"], "default")
        self.assertEqual(len(state["sessions"]), 2)
        self.assertTrue(all(s["status"] == "active" for s in state["sessions"]))
        self.assertEqual(
            next(s for s in state["sessions"] if s["account"] == "acc-1")["uses"],
            2,
        )

        out = os.path.join(self.tmp, "state.json")
        manager.export_state(out)
        with open(out, encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["sessions"], state["sessions"])


if __name__ == "__main__":
    unittest.main()
