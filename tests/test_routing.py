import unittest

from proxymesh.mesh import Mesh


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_mesh(clock=None, **config):
    clock = clock or FakeClock()
    mesh = Mesh(clock=clock, **config)
    mesh.set_pool("s", [
        {"proxy": "a1", "room": "r1"},
        {"proxy": "a2", "room": "r1"},
        {"proxy": "b1", "room": "r2"},
    ])
    return mesh, clock


class HealthTest(unittest.TestCase):
    def test_failure_lowers_health_and_success_raises_it(self):
        mesh, _ = make_mesh()
        after_fail = mesh.report("a1", False, 5.0)["health_score"]
        self.assertLess(after_fail, 1.0)
        after_ok = mesh.report("a1", True, 0.2)["health_score"]
        self.assertGreater(after_ok, after_fail)
        self.assertLess(after_ok, 1.0)

    def test_recovery_ramps_up_over_multiple_successes(self):
        mesh, _ = make_mesh()
        mesh.report("a1", False, 5.0)
        scores = []
        for _ in range(6):
            scores.append(mesh.report("a1", True, 0.2)["health_score"])
        self.assertTrue(all(0.0 < s < 1.0 for s in scores))
        self.assertEqual(scores, sorted(scores))
        self.assertLess(scores[0], 0.9)
        self.assertGreater(scores[-1], scores[0])

    def test_health_decays_toward_baseline_over_time(self):
        mesh, clock = make_mesh()
        mesh.report("a1", False, 5.0)
        low = mesh.stats("s")["rooms"]["r1"]["avg_health"]
        clock.advance(60)
        mid = mesh.stats("s")["rooms"]["r1"]["avg_health"]
        clock.advance(600)
        high = mesh.stats("s")["rooms"]["r1"]["avg_health"]
        self.assertGreater(mid, low)
        self.assertGreater(high, mid)
        self.assertGreater(high, 0.99)

    def test_pick_prefers_higher_health_over_all_others(self):
        mesh, _ = make_mesh()
        # a1 反复失败，a2 保持健康
        for _ in range(2):
            mesh.report("a1", False, 5.0)
        picked = mesh.pick("s")
        self.assertNotEqual(picked["proxy"], "a1")
        self.assertGreater(picked["health_score"], 0.0)


class LatencyTest(unittest.TestCase):
    def test_unknown_latency_proxy_is_explored_not_starved(self):
        mesh, _ = make_mesh()
        mesh.report("a1", True, 0.8)
        mesh.report("a2", True, 0.8)
        # b1 尚未测过延迟：乐观分应让它被选一次，从而拿到 rtt
        self.assertEqual(mesh.pick("s")["proxy"], "b1")

    def test_pick_prefers_lower_latency_among_healthy(self):
        mesh, _ = make_mesh()
        mesh.report("a1", True, 0.10)   # 快
        mesh.report("b1", True, 1.20)   # 慢
        mesh.report("a2", True, 1.20)
        picked = mesh.pick("s")
        self.assertEqual(picked["proxy"], "a1")
        self.assertLess(picked["rtt"], 0.2)

    def test_rtt_uses_ewma_not_last_sample(self):
        mesh, _ = make_mesh()
        mesh.report("a1", True, 1.0)
        mesh.report("a1", True, 0.1)
        # EWMA 不会直接跳到 0.1，快速出口不会因为一次抖动被误判
        self.assertGreater(mesh.stats("s")["rooms"]["r1"]["best_rtt"], 0.1)
        self.assertLess(mesh.stats("s")["rooms"]["r1"]["best_rtt"], 1.0)

    def test_selection_is_deterministic_not_random(self):
        mesh, _ = make_mesh()
        first = mesh.pick("s")["proxy"]
        second = mesh.pick("s")["proxy"]
        self.assertEqual(first, second)


class CooldownTest(unittest.TestCase):
    def test_consecutive_failures_cool_proxy(self):
        mesh, _ = make_mesh()
        mesh.pick("s")
        first = mesh.report("a1", False, 5.0)
        self.assertFalse(first["cooling"])
        self.assertFalse(first["switched"])
        second = mesh.report("a1", False, 5.0)
        self.assertTrue(second["cooling"])
        self.assertTrue(second["switched"])
        self.assertNotEqual(second["selected"], "a1")

    def test_cooling_proxy_is_excluded_from_scoring(self):
        mesh, _ = make_mesh()
        for _ in range(2):
            mesh.report("a1", False, 5.0)
        picked = mesh.pick("s")
        a1 = [c for c in picked["candidates"] if c["proxy"] == "a1"][0]
        self.assertFalse(a1["eligible"])
        self.assertIsNone(a1["score"])
        self.assertNotEqual(picked["proxy"], "a1")

    def test_success_does_not_clear_cooldown_early(self):
        mesh, clock = make_mesh()
        mesh.report("a1", False, 5.0)
        mesh.report("a1", False, 5.0)
        self.assertTrue(mesh.report("a1", True, 0.1)["cooling"])
        a1 = [c for c in mesh.pick("s")["candidates"] if c["proxy"] == "a1"][0]
        self.assertFalse(a1["eligible"])
        clock.advance(31)
        a1 = [c for c in mesh.pick("s")["candidates"] if c["proxy"] == "a1"][0]
        self.assertTrue(a1["eligible"])

    def test_failures_while_cooling_do_not_extend_cooldown(self):
        mesh, clock = make_mesh()
        mesh.report("a1", False, 5.0)
        mesh.report("a1", False, 5.0)
        until = mesh._proxies["a1"].cooldown_until
        clock.advance(10)
        mesh.report("a1", False, 5.0)
        self.assertEqual(mesh._proxies["a1"].cooldown_until, until)

    def test_cooldown_expires_and_proxy_returns(self):
        mesh, clock = make_mesh()
        mesh.report("a1", False, 5.0)
        self.assertTrue(mesh.report("a1", False, 5.0)["cooling"])
        a1 = [c for c in mesh.pick("s")["candidates"] if c["proxy"] == "a1"][0]
        self.assertFalse(a1["eligible"])
        clock.advance(31)
        a1 = [c for c in mesh.pick("s")["candidates"] if c["proxy"] == "a1"][0]
        self.assertTrue(a1["eligible"])
        self.assertFalse(a1["cooling"])


    def test_sporadic_failures_outside_window_do_not_cool(self):
        mesh, clock = make_mesh(fail_window=10.0)
        for _ in range(3):
            mesh.report("a1", False, 5.0)
            clock.advance(30)
        result = mesh.report("a1", False, 5.0)
        self.assertFalse(result["cooling"])
        self.assertFalse(result["room_cooling"])
        self.assertFalse(result["switched"])

    def test_failure_counter_resets_after_success(self):
        mesh, _ = make_mesh()
        mesh.report("a1", False, 5.0)
        mesh.report("a1", True, 0.2)
        result = mesh.report("a1", False, 5.0)
        self.assertFalse(result["cooling"])


class RoomSwitchTest(unittest.TestCase):
    def test_room_level_failure_switches_whole_room(self):
        mesh, _ = make_mesh()
        mesh.pick("s")                       # active = a1
        mesh.report("a1", False, 5.0)
        mesh.report("a1", False, 5.0)
        result = mesh.report("a1", False, 5.0)
        self.assertTrue(result["room_cooling"])
        self.assertTrue(result["switched"])
        self.assertEqual(result["selected"], "b1")
        rooms = mesh.stats("s")["rooms"]
        self.assertTrue(rooms["r1"]["cooling_room"])
        self.assertFalse(rooms["r2"]["cooling_room"])

    def test_pick_after_room_failure_stays_in_healthy_room(self):
        mesh, _ = make_mesh()
        mesh.pick("s")
        for _ in range(3):
            mesh.report("a1", False, 5.0)
        picked = mesh.pick("s")
        self.assertEqual(picked["proxy"], "b1")
        self.assertEqual(picked["room"], "r2")
        self.assertIn("机房", picked["reason"])

    def test_brief_room_recovery_does_not_break_room_cooldown(self):
        mesh, clock = make_mesh()
        mesh.pick("s")
        for _ in range(3):
            mesh.report("a1", False, 5.0)
        self.assertTrue(mesh.stats("s")["rooms"]["r1"]["cooling_room"])
        # 机房冷却期内偶发一次成功，不应让整机房立刻恢复
        result = mesh.report("a1", True, 0.1)
        self.assertTrue(result["room_cooling"])
        self.assertEqual(mesh.pick("s")["room"], "r2")
        clock.advance(61)
        self.assertFalse(mesh.stats("s")["rooms"]["r1"]["cooling_room"])

    def test_manual_lock_keeps_traffic_in_room(self):
        mesh, _ = make_mesh()
        mesh.report("b1", True, 0.05)        # b1 更快
        mesh.lock_room("s", "r1")
        picked = mesh.pick("s")
        self.assertEqual(picked["room"], "r1")
        self.assertEqual(picked["reason_code"], "locked")
        mesh.unlock_room("s")
        self.assertEqual(mesh.pick("s")["proxy"], "b1")

    def test_room_switching_respects_lock(self):
        mesh, _ = make_mesh()
        mesh.lock_room("s", "r1")
        mesh.pick("s")
        for _ in range(3):
            mesh.report("a1", False, 5.0)
        picked = mesh.pick("s")
        # 锁定的机房全冷却时，只能应急留在机房内，不能跨机房
        self.assertEqual(picked["room"], "r1")
        self.assertEqual(picked["reason_code"], "locked_emergency")


class SelectionLogTest(unittest.TestCase):
    def test_log_keeps_candidates_and_reason(self):
        mesh, _ = make_mesh()
        mesh.pick("s")
        for _ in range(2):
            mesh.report("a1", False, 5.0)
        mesh.pick("s")
        entry = mesh.selection_log("s")[-1]
        self.assertEqual(len(entry["candidates"]), 3)
        self.assertTrue(entry["reason"])
        self.assertEqual(entry["reason_code"], "best")
        names = {c["proxy"] for c in entry["candidates"]}
        self.assertEqual(names, {"a1", "a2", "b1"})

    def test_log_records_failover_reason(self):
        mesh, _ = make_mesh()
        mesh.pick("s")
        mesh.report("a1", False, 5.0)
        mesh.report("a1", False, 5.0)
        failovers = [e for e in mesh.selection_log("s")
                     if e["reason_code"] == "failover"]
        self.assertEqual(len(failovers), 1)
        self.assertTrue(failovers[0]["switched"])
        self.assertTrue(failovers[0]["candidates"])

    def test_log_is_bounded(self):
        mesh, _ = make_mesh(log_limit=3)
        for _ in range(10):
            mesh.pick("s")
        self.assertEqual(len(mesh.selection_log("s")), 3)


class InterfaceTest(unittest.TestCase):
    def test_report_shape_is_backwards_compatible(self):
        mesh, _ = make_mesh()
        result = mesh.report("a1", False, 2.0)
        for key in ("proxy", "ok", "rtt", "health", "switched"):
            self.assertIn(key, result)
        self.assertIsNone(result["health"])
        self.assertFalse(result["switched"])

    def test_pick_shape_is_backwards_compatible(self):
        mesh, _ = make_mesh()
        result = mesh.pick("s")
        for key in ("site", "proxy", "health", "reason"):
            self.assertIn(key, result)
        self.assertIsNone(result["health"])
        self.assertIn(result["reason"], result["reason"])


class PoolTest(unittest.TestCase):
    def test_set_pool_replaces_and_clears_stale_state(self):
        mesh, _ = make_mesh()
        mesh.pick("s")
        mesh.report("a1", False, 5.0)
        mesh.set_pool("s", [{"proxy": "c1", "room": "r3"}])
        picked = mesh.pick("s")
        self.assertEqual(picked["proxy"], "c1")
        self.assertEqual(set(mesh.stats("s")["rooms"]), {"r3"})
        self.assertIsNone(mesh._proxies.get("a1"))

    def test_default_pool_spans_two_rooms(self):
        mesh = Mesh()
        picked = mesh.pick("fresh")
        self.assertIn(picked["proxy"], ("proxy-a", "proxy-b"))
        rooms = {c["proxy"]: c["room"] for c in picked["candidates"]}
        self.assertEqual(len(set(rooms.values())), 2)

    def test_unknown_config_key_is_rejected(self):
        with self.assertRaises(ValueError):
            Mesh(no_such_option=1)


if __name__ == "__main__":
    unittest.main()
