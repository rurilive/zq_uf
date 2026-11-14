import unittest

from jsrender import wait
from jsrender.render import (
    MAX_NESTING,
    all_of,
    any_of,
    network_idle,
    script,
    selector,
    text,
)


class RenderTest(unittest.TestCase):
    def test_condition_true(self):
        self.assertTrue(wait({"ready": True}, lambda p: p["ready"])["ok"])

    def test_condition_false(self):
        self.assertFalse(wait({"ready": False}, lambda p: p["ready"])["ok"])

    def test_fixed_wait(self):
        self.assertEqual(wait({}, lambda p: True)["waited"], 2.0)


class FakeClock:
    """可注入的单调时钟：sleep 只推进虚拟时间，不产生真实等待。"""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        if seconds < 0:
            raise ValueError("sleep 时长不能为负")
        self.now += seconds


class FakePage:
    """模拟慢页面：每个特征在指定的虚拟时钟时刻才就绪。"""

    def __init__(self, clock, *, selectors=None, texts=None, scripts=None, idle_at=None):
        self._clock = clock
        self.selectors = selectors or {}   # css -> 出现时刻
        self.texts = texts or {}           # 子串 -> 出现时刻
        self.scripts = scripts or {}       # 脚本 -> 完成时刻
        self.idle_at = idle_at             # 进入网络空闲的时刻，None 表示始终忙碌
        self.probes = []

    def exists(self, css):
        self.probes.append(("selector", css))
        return self._clock() >= self.selectors.get(css, float("inf"))

    def text_contains(self, substr):
        self.probes.append(("text", substr))
        return self._clock() >= self.texts.get(substr, float("inf"))

    def network_idle(self):
        self.probes.append(("network_idle", None))
        return self.idle_at is not None and self._clock() >= self.idle_at

    def eval_script(self, code):
        self.probes.append(("script", code))
        return self._clock() >= self.scripts.get(code, float("inf"))

    def snapshot(self):
        now = self._clock()
        return {
            "selectors": {css: now >= at for css, at in self.selectors.items()},
            "text": sorted(s for s, at in self.texts.items() if now >= at),
            "network_idle": self.idle_at is not None and now >= self.idle_at,
            "scripts": {code: now >= at for code, at in self.scripts.items()},
        }


class TestCombinedConditions(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def _wait(self, page, condition, timeout=0.5, interval=0.05):
        return wait(page, condition, timeout, interval,
                    clock=self.clock, sleep=self.clock.sleep)

    def test_selector_hit(self):
        page = FakePage(self.clock, selectors={".card": 0.07})
        result = self._wait(page, selector(".card"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["hit"], ["selector:.card#0"])
        self.assertLess(result["elapsed"], 0.2)

    def test_text_hit(self):
        page = FakePage(self.clock, texts={"加载完成": 0.06})
        result = self._wait(page, text("加载完成"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["hit"], ["text:加载完成#0"])

    def test_network_idle_hit(self):
        page = FakePage(self.clock, idle_at=0.08)
        result = self._wait(page, network_idle())
        self.assertTrue(result["ok"])
        self.assertEqual(result["hit"], ["network_idle#0"])

    def test_network_idle_busy_invert(self):
        # 要求页面保持忙碌（idle=False）：始终忙碌的页面首轮即命中
        page = FakePage(self.clock, idle_at=None)
        result = self._wait(page, network_idle(False))
        self.assertTrue(result["ok"])
        self.assertEqual(result["polls"], 1)
        self.assertEqual(result["elapsed"], 0.0)

    def test_script_hit(self):
        page = FakePage(self.clock, scripts={"window.__done": 0.09})
        result = self._wait(page, script("window.__done"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["hit"], ["script:window.__done#0"])

    def test_all_of_both_conditions(self):
        page = FakePage(self.clock, selectors={".card": 0.1}, texts={"done": 0.15})
        result = self._wait(page, all_of(selector(".card"), text("done")))
        self.assertTrue(result["ok"])
        # 两个叶子在同一轮命中时，按声明顺序（selector 在前）排列
        self.assertEqual(result["hit"], ["selector:.card#0", "text:done#1"])

    def test_any_of_first_ready_wins(self):
        page = FakePage(self.clock, selectors={".card": 0.3}, texts={"done": 0.05})
        result = self._wait(page, any_of(selector(".card"), text("done")))
        self.assertTrue(result["ok"])
        self.assertEqual(result["hit"], ["text:done#1"])

    def test_nested_combo(self):
        # (选择器 且 文本) 或 网络空闲：空闲先到，走 or 分支
        page = FakePage(
            self.clock,
            selectors={".card": 0.4},
            texts={"done": 0.4},
            idle_at=0.1,
        )
        condition = any_of(
            all_of(selector(".card"), text("done")),
            network_idle(),
        )
        result = self._wait(page, condition)
        self.assertTrue(result["ok"])
        self.assertEqual(result["hit"], ["network_idle#2"])
        self.assertEqual(result["condition"]["any"][1], {"type": "network_idle", "target": True})

    def test_invert_selector_absent(self):
        page = FakePage(self.clock)  # 选择器永远不出现
        result = self._wait(page, {"type": "selector", "target": ".popup", "invert": True})
        self.assertTrue(result["ok"])
        self.assertEqual(result["polls"], 1)

    def test_legacy_callable_still_works(self):
        page = FakePage(self.clock, idle_at=0.1)
        result = self._wait(page, lambda p: p.network_idle())
        self.assertTrue(result["ok"])
        self.assertEqual(result["condition"]["type"], "callable")
        self.assertEqual(result["hit"], ["callable#0"])

    def test_dict_page_fallback(self):
        page = {
            "selectors": {".card": True},
            "text": "hello world",
            "network_idle": True,
            "scripts": {"window.__done": True},
        }
        self.assertTrue(self._wait(page, selector(".card"))["ok"])
        self.assertTrue(self._wait(page, text("hello"))["ok"])
        self.assertTrue(self._wait(page, network_idle())["ok"])
        self.assertTrue(self._wait(page, script("window.__done"))["ok"])


class TestBudgetAdjustment(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def _wait(self, page, condition, timeout, interval):
        return wait(page, condition, timeout, interval,
                    clock=self.clock, sleep=self.clock.sleep)

    def test_delay_shrinks_with_remaining_budget(self):
        # 永不满足的条件，预算 0.25、间隔 0.1：最后一段必须收缩到 0.05，
        # 预算边界再做一次探测（共 4 轮）后退出
        page = FakePage(self.clock, selectors={".card": float("inf")})
        result = self._wait(page, selector(".card"), 0.25, 0.1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["delays"], [0.1, 0.1, 0.05])
        self.assertEqual(result["polls"], 4)
        self.assertAlmostEqual(result["elapsed"], 0.25)

    def test_interval_equals_timeout_fills_budget(self):
        page = FakePage(self.clock)
        result = self._wait(page, network_idle(), 0.1, 0.1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["polls"], 2)
        self.assertEqual(result["delays"], [0.1])
        self.assertEqual(result["elapsed"], 0.1)

    def test_last_probe_hits_at_budget_boundary(self):
        # 特征在 0.23 就绪，预算 0.25：预算边界的最后一轮探测必须能命中
        page = FakePage(self.clock, selectors={".card": 0.23})
        result = self._wait(page, selector(".card"), 0.25, 0.1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["delays"], [0.1, 0.1, 0.05])
        self.assertAlmostEqual(result["elapsed"], 0.25)

    def test_fast_page_returns_immediately(self):
        page = FakePage(self.clock, selectors={".card": 0.0})
        result = self._wait(page, selector(".card"), 0.5, 0.1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["polls"], 1)
        self.assertEqual(result["delays"], [])
        self.assertEqual(result["elapsed"], 0.0)
        self.assertEqual(result["waited"], 0.5)

    def test_injected_clock_costs_no_real_time(self):
        import time as real_time

        page = FakePage(self.clock, selectors={".card": float("inf")})
        start = real_time.monotonic()
        result = self._wait(page, selector(".card"), 1.0, 0.05)
        wall = real_time.monotonic() - start
        self.assertFalse(result["ok"])
        self.assertAlmostEqual(result["elapsed"], 1.0)
        self.assertLess(wall, 0.5)

    def test_interval_greater_than_timeout_rejected(self):
        with self.assertRaises(ValueError):
            self._wait(FakePage(self.clock), network_idle(), 0.1, 0.2)


class TestTimeoutSnapshot(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def test_timeout_diff_reports_changes(self):
        page = FakePage(
            self.clock,
            selectors={".card": 0.1},
            texts={"done": 0.2},
            idle_at=0.3,  # 预算内不会空闲
        )
        result = wait(page, all_of(selector(".card"), text("done"), network_idle()),
                      0.25, 0.05, clock=self.clock, sleep=self.clock.sleep)

        self.assertFalse(result["ok"])
        self.assertEqual(result["waited"], 0.25)
        self.assertAlmostEqual(result["elapsed"], 0.25)

        # 最后状态：选择器与文本已命中，网络空闲未命中
        states = {item["id"]: item["matched"] for item in result["last_state"]}
        self.assertTrue(states["selector:.card#0"])
        self.assertTrue(states["text:done#1"])
        self.assertFalse(states["network_idle#2"])
        # 部分命中的叶子写进 hit，作为超时现场线索
        self.assertEqual(set(result["hit"]), {"selector:.card#0", "text:done#1"})

        # 快照差异：首末之间发生的变化（network_idle 全程为 False，不进差异）
        diff = result["diff"]
        self.assertIn("changed", diff)
        changed = diff["changed"]
        self.assertEqual(changed["selectors"]["before"], {".card": False})
        self.assertEqual(changed["selectors"]["after"], {".card": True})
        self.assertEqual(changed["text"]["before"], [])
        self.assertEqual(changed["text"]["after"], ["done"])
        self.assertNotIn("network_idle", changed)
        self.assertNotIn("added", diff)
        self.assertNotIn("removed", diff)

        # 首末快照都在现场里
        self.assertEqual(result["first_snapshot"]["selectors"], {".card": False})
        self.assertEqual(result["snapshot"]["selectors"], {".card": True})
        self.assertFalse(result["first_snapshot"]["network_idle"])
        self.assertFalse(result["snapshot"]["network_idle"])

    def test_timeout_without_changes_has_empty_diff(self):
        page = FakePage(self.clock, selectors={".card": float("inf")})
        result = wait(page, selector(".card"), 0.2, 0.05,
                      clock=self.clock, sleep=self.clock.sleep)
        self.assertFalse(result["ok"])
        self.assertEqual(result["diff"], {})
        self.assertEqual(result["first_snapshot"], result["snapshot"])
        self.assertNotIn("hit", result)

    def test_timeout_scene_fields_complete(self):
        page = FakePage(self.clock, idle_at=None)
        result = wait(page, network_idle(), 0.15, 0.05,
                      clock=self.clock, sleep=self.clock.sleep)
        for field in ("ok", "waited", "elapsed", "polls", "delays",
                      "condition", "last_state", "first_snapshot",
                      "snapshot", "diff"):
            self.assertIn(field, result)


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def _wait(self, page, condition, **kwargs):
        kwargs.setdefault("timeout", 0.1)
        kwargs.setdefault("interval", 0.02)
        return wait(page, condition, clock=self.clock, sleep=self.clock.sleep, **kwargs)

    def test_non_dict_non_callable_condition(self):
        with self.assertRaises(ValueError):
            self._wait({}, 123)
        with self.assertRaises(ValueError):
            self._wait({}, None)

    def test_unknown_condition_type(self):
        with self.assertRaises(ValueError):
            self._wait({}, {"type": "xpath", "target": "//div"})

    def test_leaf_missing_target(self):
        with self.assertRaises(ValueError):
            self._wait({}, {"type": "selector"})
        with self.assertRaises(ValueError):
            self._wait({}, {"type": "script", "target": "  "})
        with self.assertRaises(ValueError):
            self._wait({}, {"type": "text", "target": 42})

    def test_network_idle_requires_bool(self):
        with self.assertRaises(ValueError):
            self._wait({}, {"type": "network_idle", "target": "yes"})

    def test_invalid_invert(self):
        with self.assertRaises(ValueError):
            self._wait({}, {"type": "selector", "target": ".x", "invert": 1})

    def test_empty_combo(self):
        with self.assertRaises(ValueError):
            self._wait({}, {"all": []})
        with self.assertRaises(ValueError):
            self._wait({}, {"any": []})

    def test_all_and_any_together(self):
        with self.assertRaises(ValueError):
            self._wait({}, {"all": [selector(".x")], "any": [text("y")]})

    def test_combo_must_be_list(self):
        with self.assertRaises(ValueError):
            self._wait({}, {"all": selector(".x")})

    def test_unknown_extra_field(self):
        with self.assertRaises(ValueError):
            self._wait({}, {"type": "selector", "target": ".x", "timeout": 1})
        with self.assertRaises(ValueError):
            self._wait({}, {"all": [selector(".x")], "op": "and"})

    def test_bad_timeout_and_interval(self):
        with self.assertRaises(ValueError):
            self._wait({}, text("x"), timeout=0)
        with self.assertRaises(ValueError):
            self._wait({}, text("x"), timeout=-1)
        with self.assertRaises(ValueError):
            self._wait({}, text("x"), timeout=True)
        with self.assertRaises(ValueError):
            self._wait({}, text("x"), timeout="1")
        with self.assertRaises(ValueError):
            self._wait({}, text("x"), interval=0)
        with self.assertRaises(ValueError):
            self._wait({}, text("x"), interval=0.05, timeout=0.01)

    def test_nesting_depth_limit(self):
        condition = {"type": "selector", "target": ".x"}
        for _ in range(MAX_NESTING + 1):
            condition = {"all": [condition]}
        with self.assertRaises(ValueError):
            self._wait({}, condition)

    def test_page_without_protocol_raises_type_error(self):
        with self.assertRaises(TypeError):
            self._wait(object(), selector(".x"))


if __name__ == "__main__":
    unittest.main()
