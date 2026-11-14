import json
import os
import subprocess
import sys
import tempfile
import unittest

from hostshield import INTERVAL, acquire, release
from hostshield import shield


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ShieldTest(unittest.TestCase):
    def setUp(self):
        shield._last[0] = 0.0

    def test_fixed_interval(self):
        self.assertEqual(acquire("shop")["at"], INTERVAL)

    def test_sites_share_schedule(self):
        first = acquire("shop")["at"]
        second = acquire("news")["at"]
        self.assertEqual(second - first, INTERVAL)

    def test_release_ignores_signals(self):
        self.assertEqual(release("shop", 5.0, 429)["rate"], 1.0)


class _StateFileTest(unittest.TestCase):
    """每个用例使用独立的临时状态文件，互不污染。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("HOSTSHIELD_STATE_FILE")
        os.environ["HOSTSHIELD_STATE_FILE"] = os.path.join(
            self._tmp.name, "state.json"
        )
        shield.reset()

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("HOSTSHIELD_STATE_FILE", None)
        else:
            os.environ["HOSTSHIELD_STATE_FILE"] = self._old_env
        self._tmp.cleanup()


class SlowdownRecoveryTest(_StateFileTest):
    def test_consecutive_errors_slowdown_then_recover(self):
        # 连续 429 达到阈值：速率减半、并发上限下调
        first = release("shop", 0.1, 429)
        self.assertEqual(first["rate"], 1.0)  # 单次错误不立即降速
        second = release("shop", 0.1, 429)
        self.assertEqual(second["rate"], 0.5)
        self.assertEqual(second["concurrency"], 3)
        self.assertIn("错误", second["reason"])

        # 5xx 与 429 同属硬故障，连续计数跨状态码累计
        reset_state = release("api", 0.1, 500)
        self.assertEqual(reset_state["rate"], 1.0)
        hit = release("api", 0.1, 503)
        self.assertEqual(hit["rate"], 0.5)

        # 降速后调度间隔拉大到 1/rate
        granted = acquire("shop")
        self.assertTrue(granted["granted"])
        self.assertEqual(granted["at"], 2.0)  # 1 / 0.5

        # 连续成功达到恢复门槛：缓慢提速并恢复并发
        for _ in range(shield.RECOVER_STREAK):
            recovered = release("shop", 0.1, 200)
        self.assertEqual(recovered["rate"], 0.55)  # 0.5 * 1.1
        self.assertEqual(recovered["concurrency"], 4)
        self.assertIn("恢复", recovered["reason"])
        self.assertIn("速率", recovered["reason"])

    def test_rtt_pressure_slowdown(self):
        # 首次上报建立 RTT 基线
        release("slow", 0.1, 200)
        # RTT 远超基线 2 倍：按 RTT 因子降速
        info = release("slow", 1.0, 200)
        self.assertEqual(info["rate"], 0.9)  # 1.0 * 0.9
        self.assertEqual(info["concurrency"], 3)
        self.assertIn("RTT", info["reason"])
        self.assertIsNotNone(info["rtt_ema"])

    def test_low_success_rate_window_slowdown(self):
        # 10 次请求成败交替（无连续失败，不会触发硬故障降速），成功率恰好 50%，
        # 阈值是严格低于 50%，因此不触发
        for i in range(10):
            release("edge", 0.1, 500 if i % 2 == 0 else 200)
        self.assertEqual(shield.status("edge")["rate"], 1.0)

        # 另一个站点：同样的交替序列再补一次失败，窗口 5 成功 / 11 总 ≈ 45%，
        # 且仍无连续故障，这次由成功率窗口触发降速
        for i in range(10):
            release("flaky", 0.1, 500 if i % 2 == 0 else 200)
        info = release("flaky", 0.1, 500)
        self.assertLess(info["rate"], 1.0)
        self.assertIn("成功率", info["reason"])

    def test_bounds_are_respected(self):
        # 持续故障不会击穿下限
        for _ in range(30):
            release("dying", 0.1, 429)
        info = shield.status("dying")
        self.assertGreaterEqual(info["rate"], shield.MIN_RATE)
        self.assertGreaterEqual(info["concurrency"], shield.MIN_CONCURRENCY)

        # 持续成功恢复不会超过上限
        for _ in range(60):
            release("dying", 0.1, 200)
        info = shield.status("dying")
        self.assertLessEqual(info["rate"], shield.MAX_RATE)
        self.assertLessEqual(info["concurrency"], shield.MAX_CONCURRENCY)


class SiteIsolationTest(_StateFileTest):
    def test_sites_keep_independent_state(self):
        release("a", 0.1, 429)
        release("a", 0.1, 429)
        a = shield.status("a")
        self.assertEqual(a["rate"], 0.5)
        self.assertEqual(a["concurrency"], 3)

        # B 站保持默认参数，A 站的故障与 B 无关
        b = release("b", 0.1, 200)
        self.assertEqual(b["rate"], 1.0)
        self.assertEqual(b["concurrency"], 4)
        self.assertNotIn("错误", b["reason"])

        # B 站后续故障不回灌到 A 站
        a_reasons_before = len(shield.status("a")["reasons"])
        release("b", 0.1, 429)
        release("b", 0.1, 429)
        a_after = shield.status("a")
        self.assertEqual(a_after["rate"], 0.5)
        self.assertEqual(len(a_after["reasons"]), a_reasons_before)
        self.assertEqual(shield.status("b")["rate"], 0.5)

        # 全量状态里两站条目相互独立
        all_sites = shield.status()
        self.assertEqual(set(all_sites), {"a", "b"})


class MultiProcessTest(_StateFileTest):
    def test_state_shared_across_processes(self):
        # 子进程内调大恢复门槛，保证 5 次成功不会触发自适应恢复，
        # 从而隔离地验证“共享状态下合计不超速”
        child = (
            "import json, sys;"
            "sys.path.insert(0, %r);"
            "from hostshield import acquire, release;"
            "from hostshield import shield;"
            "shield.RECOVER_STREAK = 10 ** 6;"
            "ats = [];"
            "[(ats.append(acquire('shared')['at']),"
            " release('shared', 0.1, 200))"
            " for i in range(5)];"
            "print(json.dumps(ats))"
        ) % REPO_ROOT

        procs = [
            subprocess.Popen(
                [sys.executable, "-c", child],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=os.environ.copy(),
            )
            for _ in range(2)
        ]
        all_ats = []
        for proc in procs:
            out, err = proc.communicate(timeout=30)
            self.assertEqual(proc.returncode, 0, err.decode())
            ats = json.loads(out.decode())
            self.assertEqual(len(ats), 5)
            self.assertEqual(ats, sorted(ats))  # 单进程内严格递增
            all_ats.extend(ats)

        # 两进程合计 10 个许可，虚拟时刻恰好铺满 1.0..10.0：
        # 无重复发放（不超并发）也无跳号（不超速率）
        self.assertEqual(sorted(all_ats), [float(i) for i in range(1, 11)])

        # 父进程能读到子进程写回的共享状态，并接在同一调度序列之后
        shared = shield.status("shared")
        self.assertEqual(shared["in_flight"], 0)
        self.assertEqual(shared["rate"], 1.0)
        self.assertEqual(acquire("shared")["at"], 11.0)

    def test_slowdown_visible_to_other_process(self):
        # 进程 A 制造连续故障降速并落盘，进程 B 冷启动后看到同一参数
        slow = (
            "import sys;"
            "sys.path.insert(0, %r);"
            "from hostshield import release;"
            "release('shared2', 0.1, 429);"
            "release('shared2', 0.1, 429)"
        ) % REPO_ROOT
        read = (
            "import json, sys;"
            "sys.path.insert(0, %r);"
            "from hostshield import shield;"
            "print(json.dumps(shield.status('shared2')))"
        ) % REPO_ROOT

        first = subprocess.run(
            [sys.executable, "-c", slow],
            capture_output=True, text=True, timeout=30, env=os.environ.copy(),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = subprocess.run(
            [sys.executable, "-c", read],
            capture_output=True, text=True, timeout=30, env=os.environ.copy(),
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        info = json.loads(second.stdout)
        self.assertEqual(info["rate"], 0.5)
        self.assertEqual(info["concurrency"], 3)
        self.assertIn("错误", info["reason"])

    def test_state_file_persists_across_reopen(self):
        release("persisted", 0.1, 429)
        release("persisted", 0.1, 429)
        # 重新打开状态文件（模拟新进程冷启动）
        with shield._Store() as state:
            self.assertIn("persisted", state["sites"])
            self.assertEqual(state["sites"]["persisted"]["rate"], 0.5)


class ValidationTest(_StateFileTest):
    def test_acquire_validates_site(self):
        for bad in ("", 123, None, ["x"]):
            with self.assertRaises(ValueError):
                acquire(bad)

    def test_release_validates_arguments(self):
        for bad_rtt in (-0.1, "0.1", True, None):
            with self.assertRaises(ValueError):
                release("ok", bad_rtt, 200)
        for bad_status in (99, 600, 200.0, "200", True, None):
            with self.assertRaises(ValueError):
                release("ok", 0.1, bad_status)
        with self.assertRaises(ValueError):
            release("", 0.1, 200)

    def test_valid_inputs_accepted(self):
        granted = acquire("ok")
        self.assertTrue(granted["granted"])
        info = release("ok", 0, 200)  # rtt=0 合法
        self.assertEqual(info["site"], "ok")
        self.assertEqual(info["in_flight"], 0)

    def test_acquire_denied_when_concurrency_full(self):
        for _ in range(shield.DEFAULT_CONCURRENCY):
            self.assertTrue(acquire("busy")["granted"])
        denied = acquire("busy")
        self.assertFalse(denied["granted"])
        self.assertGreaterEqual(denied["wait"], 0.0)
        # 归还一个许可后可以继续申请
        release("busy", 0.1, 200)
        self.assertTrue(acquire("busy")["granted"])


if __name__ == "__main__":
    unittest.main()
