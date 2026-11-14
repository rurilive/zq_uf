import unittest

from proxyrotate import (
    PROXIES,
    ProxyRotator,
    REASON_FAILURE,
    REASON_SWITCHBACK,
    acquire,
    get_rotator,
    release,
)


class RotateTest(unittest.TestCase):
    def test_acquire_in_pool(self):
        self.assertIn(acquire("s1"), PROXIES)

    def test_release_reports(self):
        self.assertTrue(release("s1", PROXIES[0], True)["ok"])

    def test_release_keeps_session(self):
        self.assertEqual(release("s1", PROXIES[0], False)["session"], "s1")


class FakeClock:
    """可手动推进的假时钟，测试不依赖真实时间。"""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_rotator(**kwargs):
    clock = FakeClock()
    defaults = dict(
        site="test",
        failure_threshold=3,
        cooldown=30.0,
        session_ttl=60.0,
        clock=clock,
    )
    defaults.update(kwargs)
    return ProxyRotator(**defaults), clock


class SessionBindingTest(unittest.TestCase):
    def test_same_proxy_within_ttl(self):
        rotator, _ = make_rotator()
        first = rotator.acquire("s1")
        # 同一会话跨多次请求，绑定有效期内出口固定（登录态不丢）
        for _ in range(5):
            self.assertEqual(rotator.acquire("s1"), first)
        self.assertEqual(rotator.bindings(), {"s1": first})

    def test_successful_release_keeps_binding(self):
        rotator, _ = make_rotator()
        proxy = rotator.acquire("s1")
        rotator.release("s1", proxy, True)
        self.assertEqual(rotator.acquire("s1"), proxy)
        # 绑定期内正常使用不产生任何切换记录
        self.assertEqual(rotator.rotations(), [])
        self.assertEqual(rotator.switchbacks(), [])
        self.assertIsNone(rotator.last_switch_reason("s1"))

    def test_binding_expires_but_healthy_proxy_is_renewed_silently(self):
        rotator, clock = make_rotator()
        proxy = rotator.acquire("s1")
        clock.advance(61)  # 超过 TTL
        # 原出口仍健康：静默续期，不算切换
        self.assertEqual(rotator.acquire("s1"), proxy)
        self.assertEqual(rotator.rotations(), [])
        self.assertEqual(rotator.switchbacks(), [])


class FailureRotationTest(unittest.TestCase):
    def test_below_threshold_does_not_rotate(self):
        rotator, _ = make_rotator(failure_threshold=3)
        proxy = rotator.acquire("s1")
        for _ in range(2):
            result = rotator.release("s1", proxy, False)
            self.assertFalse(result["rotated"])
        # 未达阈值，绑定不动
        self.assertEqual(rotator.acquire("s1"), proxy)
        self.assertEqual(rotator.cooldowns(), {})

    def test_threshold_rotates_and_records_reason(self):
        rotator, _ = make_rotator(failure_threshold=3)
        old = rotator.acquire("s1")
        for i in range(3):
            result = rotator.release("s1", old, False)
        # 最后一次失败触发轮换
        self.assertTrue(result["rotated"])
        self.assertEqual(result["reason"], REASON_FAILURE)
        self.assertIn(old, rotator.cooldowns())

        new = rotator.acquire("s1")
        self.assertNotEqual(new, old)
        self.assertIn(new, PROXIES)
        # 解绑后重新 acquire，当前绑定恢复
        self.assertEqual(rotator.bindings(), {"s1": new})
        # 冷却中的失败出口不会被再选中：任何新会话拿到的都不是它
        for i in range(9):
            self.assertNotEqual(rotator.acquire(f"other-{i}"), old)

        rotations = rotator.rotations()
        self.assertEqual(len(rotations), 1)
        self.assertEqual(rotations[0]["old_proxy"], old)
        self.assertEqual(rotations[0]["new_proxy"], new)
        self.assertEqual(rotations[0]["reason"], REASON_FAILURE)
        self.assertEqual(rotator.last_switch_reason("s1"), REASON_FAILURE)

    def test_success_resets_failure_count(self):
        rotator, _ = make_rotator(failure_threshold=3)
        proxy = rotator.acquire("s1")
        rotator.release("s1", proxy, False)
        rotator.release("s1", proxy, False)
        rotator.release("s1", proxy, True)  # 成功清零
        rotator.release("s1", proxy, False)
        rotator.release("s1", proxy, False)
        # 连续失败重新计数，未达阈值不冷却
        self.assertEqual(rotator.cooldowns(), {})
        self.assertEqual(rotator.acquire("s1"), proxy)

    def test_cooldown_detaches_all_sessions_on_proxy(self):
        rotator, clock = make_rotator(
            failure_threshold=1, cooldown=30.0, session_ttl=5.0
        )
        # s1 先占第一个出口并等它冷却，之后两个新会话都会选到第二个出口
        first = rotator.acquire("s1")
        rotator.release("s1", first, False)
        shared = rotator.acquire("s2")      # PROXIES[1]
        clock.advance(6)                     # s1 绑定过期
        self.assertEqual(rotator.acquire("s1"), shared)  # s1 也落到 shared
        self.assertEqual(
            rotator.bindings(), {"s1": shared, "s2": shared}
        )
        # shared 进冷却，两个会话都应被解绑
        rotator.release("s1", shared, False)
        self.assertEqual(rotator.bindings(), {})
        self.assertNotEqual(rotator.acquire("s1"), shared)
        self.assertNotEqual(rotator.acquire("s2"), shared)

    def test_all_proxies_cooldown_falls_back_to_earliest(self):
        rotator, clock = make_rotator(
            failure_threshold=1, cooldown=30.0, session_ttl=1000.0
        )
        # 三个出口依次打挂，全部进入冷却
        p = rotator.acquire("s1")
        rotator.release("s1", p, False)
        p = rotator.acquire("s1")
        rotator.release("s1", p, False)
        p = rotator.acquire("s1")
        rotator.release("s1", p, False)
        # 无健康出口，仍返回一个兜底出口，而不是报错
        fallback = rotator.acquire("s1")
        self.assertIn(fallback, PROXIES)
        self.assertEqual(
            rotator.last_switch_reason("s1"), "all_proxies_cooldown"
        )


class CooldownSwitchbackTest(unittest.TestCase):
    def test_switchback_after_cooldown_and_ttl(self):
        rotator, clock = make_rotator(
            failure_threshold=2, cooldown=30.0, session_ttl=60.0
        )
        original = rotator.acquire("s1")  # PROXIES[0]
        rotator.release("s1", original, False)
        rotator.release("s1", original, False)  # 进冷却，会话解绑
        self.assertIn(original, rotator.cooldowns())

        replacement = rotator.acquire("s1")
        self.assertNotEqual(replacement, original)
        self.assertEqual(rotator.last_switch_reason("s1"), REASON_FAILURE)

        # 冷却结束但绑定未到期：粘在当前出口，不提前回切
        clock.advance(31)
        self.assertEqual(rotator.acquire("s1"), replacement)
        self.assertEqual(rotator.switchbacks(), [])

        # 绑定也到期：优先回切原出口
        clock.advance(30)
        self.assertEqual(rotator.acquire("s1"), original)
        switchbacks = rotator.switchbacks()
        self.assertEqual(len(switchbacks), 1)
        self.assertEqual(switchbacks[0]["old_proxy"], replacement)
        self.assertEqual(switchbacks[0]["new_proxy"], original)
        self.assertEqual(switchbacks[0]["reason"], REASON_SWITCHBACK)
        self.assertEqual(rotator.last_switch_reason("s1"), REASON_SWITCHBACK)
        # 回切是显式记录，与轮换记录分开
        self.assertEqual(len(rotator.rotations()), 1)

    def test_no_switchback_while_cooldown_active(self):
        rotator, clock = make_rotator(
            failure_threshold=2, cooldown=30.0, session_ttl=10.0
        )
        original = rotator.acquire("s1")
        rotator.release("s1", original, False)
        rotator.release("s1", original, False)
        rotator.acquire("s1")  # 换到替代出口

        clock.advance(11)  # TTL 到期但冷却未结束
        again = rotator.acquire("s1")
        self.assertNotEqual(again, original)
        self.assertEqual(rotator.switchbacks(), [])


class SiteIsolationTest(unittest.TestCase):
    def test_bindings_not_shared_between_sites(self):
        clock = FakeClock()
        site_a = ProxyRotator(site="a", clock=clock, failure_threshold=2)
        site_b = ProxyRotator(site="b", clock=clock, failure_threshold=2)

        proxy_a = site_a.acquire("same-session")
        proxy_b = site_b.acquire("same-session")
        # 同一会话标识在不同站点各自独立绑定
        self.assertEqual(site_a.bindings(), {"same-session": proxy_a})
        self.assertEqual(site_b.bindings(), {"same-session": proxy_b})

        # A 站点把出口打挂，不影响 B 站点的冷却和绑定
        for _ in range(2):
            site_a.release("same-session", proxy_a, False)
        site_a.acquire("same-session")  # 触发 A 站点轮换
        self.assertIn(proxy_a, site_a.cooldowns())
        self.assertEqual(site_b.cooldowns(), {})
        self.assertEqual(site_b.acquire("same-session"), proxy_b)

        # 记录也不跨站点
        self.assertTrue(site_a.rotations())
        self.assertEqual(site_b.rotations(), [])
        self.assertEqual(site_b.last_switch_reason("same-session"), None)

    def test_get_rotator_reuses_instance_per_site(self):
        r1 = get_rotator("shop-x", failure_threshold=5)
        r2 = get_rotator("shop-x")
        r3 = get_rotator("shop-y")
        self.assertIs(r1, r2)
        self.assertIsNot(r1, r3)


if __name__ == "__main__":
    unittest.main()
