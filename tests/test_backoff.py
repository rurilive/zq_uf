import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from crawlbackoff import budget, signal
from crawlbackoff import backoff


class FakeClock:
    """可手动推进的时钟，替换 backoff._now。"""

    def __init__(self, start=None):
        # 基于真实时间起步：跨进程子进程走真实时钟，窗口不会因基准不同而过期。
        self.t = time.time() if start is None else start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class BackoffTest(unittest.TestCase):
    def setUp(self):
        backoff._state.clear()
        self.clock = FakeClock()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        backoff.set_state_dir(self.tmp.name)
        self._now_patch = mock.patch.object(backoff, "_now", self.clock)
        self._now_patch.start()
        self.addCleanup(self._now_patch.stop)

    def test_signal_records_status(self):
        self.assertEqual(signal("shop", 429)["status"], 429)

    def test_budget_after_signal(self):
        signal("shop", 429)
        self.assertEqual(budget("shop")["remaining"], 3)

    def test_budget_default(self):
        self.assertEqual(budget("news")["status"], "ok")

    def test_429_throttles_whole_site_for_all_tasks(self):
        # 任务 A 正常请求后命中 429。
        signal("shop", 200)
        snap = signal("shop", 429)
        self.assertEqual(snap["phase"], "backoff")
        self.assertEqual(snap["remaining"], backoff.BACKOFF_BUDGET)
        self.assertGreater(snap["window_remaining"], 0)
        # 任务 B（不同调用方，同站点）立即看到全站降速，共享窗口与预算。
        view = budget("shop")
        self.assertEqual(view["phase"], "backoff")
        self.assertEqual(view["window_end"], snap["window_end"])
        self.assertEqual(view["remaining"], snap["remaining"])
        # 同站点另一个任务消耗的是共享预算。
        signal("shop", 200)
        self.assertEqual(budget("shop")["remaining"], snap["remaining"] - 1)
        # 不同站点互不影响。
        self.assertEqual(budget("news")["phase"], "normal")

    def test_stepwise_recovery_only_after_continuous_success(self):
        signal("shop", 429)
        # 窗口未结束：仍是退避态，成功也不累计恢复进度。
        signal("shop", 200)
        self.assertEqual(budget("shop")["phase"], "backoff")
        self.assertEqual(budget("shop")["consecutive_successes"], 0)

        # 窗口结束：进入恢复阶梯第一级（预算 3），而非直接满速。
        self.clock.advance(backoff.WINDOW_BASE + 1)
        first = budget("shop")
        self.assertEqual(first["phase"], "recovery")
        self.assertEqual(first["level"], 1)
        self.assertEqual(first["remaining"], backoff.RECOVERY_BUDGETS[0])
        self.assertNotEqual(first["budget"], backoff.FULL_BUDGET)

        # 连续成功达标前，级别不变。
        for _ in range(backoff.SUCCESSES_PER_STEP - 1):
            signal("shop", 200)
        self.assertEqual(budget("shop")["level"], 1)
        # 达标后升到第二级（预算 6），连续成功计数清零重来。
        signal("shop", 200)
        second = budget("shop")
        self.assertEqual(second["level"], 2)
        self.assertEqual(second["remaining"], backoff.RECOVERY_BUDGETS[1])
        self.assertEqual(second["consecutive_successes"], 0)

        # 第二级再连续成功才回满速。
        for _ in range(backoff.SUCCESSES_PER_STEP):
            signal("shop", 200)
        full = budget("shop")
        self.assertEqual(full["phase"], "normal")
        self.assertEqual(full["remaining"], backoff.FULL_BUDGET)

    def test_429_during_recovery_resets_progress(self):
        signal("shop", 429)
        self.clock.advance(backoff.WINDOW_BASE + 1)
        for _ in range(backoff.SUCCESSES_PER_STEP - 1):
            signal("shop", 200)
        # 恢复途中再次命中 429：进度清零，窗口翻倍并重新退避。
        re = signal("shop", 429)
        self.assertEqual(re["phase"], "backoff")
        self.assertEqual(re["consecutive_successes"], 0)
        self.assertEqual(re["window_seconds"], backoff.WINDOW_BASE * 2)
        self.assertEqual(re["remaining"], backoff.BACKOFF_BUDGET)

    def test_state_persists_across_restart_within_window(self):
        snap = signal("shop", 429)
        end_before = snap["window_end"]
        # 模拟重启：丢弃进程内缓存、重建协调器实例（同状态目录）。
        backoff._state.clear()
        self.clock.advance(1)
        view = budget("shop")
        self.assertEqual(view["phase"], "backoff")
        self.assertEqual(view["window_end"], end_before)
        self.assertEqual(view["window_remaining"], round(end_before - self.clock(), 3))

        # 落盘文件内容与快照一致。
        with open(os.path.join(self.tmp.name, "state.json"), encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["shop"]["window_end"], end_before)
        self.assertEqual(on_disk["shop"]["phase"], "backoff")

    def test_cross_process_sync_via_file_lock(self):
        snap = signal("shop", 429)
        env = dict(os.environ)
        env["CRAWLBACKOFF_HOME"] = self.tmp.name
        code = (
            "from crawlbackoff import budget;"
            "view = budget('shop');"
            "assert view['phase'] == 'backoff', view;"
            "assert view['window_end'] == %r, view;"
            "assert view['remaining'] == %r, view;"
            "print('ok')"
            % (snap["window_end"], snap["remaining"])
        )
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

        # 子进程写入的成功消耗也能被本进程看到（共享预算）。
        code2 = (
            "from crawlbackoff import signal, budget;"
            "signal('shop', 200);"
            "print(budget('shop')['remaining'])"
        )
        result2 = subprocess.run(
            [sys.executable, "-c", code2],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result2.returncode, 0, result2.stderr)
        expected = snap["remaining"] - 1
        self.assertEqual(int(result2.stdout.strip()), expected)
        self.assertEqual(budget("shop")["remaining"], expected)

    def test_snapshot_exposes_window_budget_and_recovery_fields(self):
        snap = signal("shop", 429)
        for key in (
            "site",
            "status",
            "phase",
            "level",
            "remaining",
            "budget",
            "window_end",
            "window_seconds",
            "window_remaining",
            "consecutive_successes",
        ):
            self.assertIn(key, snap)
        self.assertEqual(snap["site"], "shop")
        self.assertEqual(snap["budget"], backoff.BACKOFF_BUDGET)


if __name__ == "__main__":
    unittest.main()
