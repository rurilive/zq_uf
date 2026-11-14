import time
import unittest

from sessionpin import bind, rebind
from sessionpin import pin


class PinTest(unittest.TestCase):
    def setUp(self):
        pin._reset()

    def test_bind_returns_site(self):
        self.assertEqual(bind("s1", "shop")["site"], "shop")

    def test_bind_overwrites(self):
        bind("s1", "shop"); bind("s1", "news")
        self.assertEqual(bind("s1", "shop")["site"], "shop")

    def test_rebind_changes_proxy(self):
        bind("s1", "shop")
        self.assertEqual(rebind("s1", "failed")["proxy"], "10.0.0.2:80")


class StickyBindingTest(unittest.TestCase):
    def setUp(self):
        pin._reset()

    def tearDown(self):
        pin._reset()

    def test_bind_sticky_within_ttl(self):
        b1 = bind("s1", "shop")
        b2 = bind("s1", "shop")
        self.assertEqual(b1["proxy"], b2["proxy"])
        self.assertEqual(b1["expires"], b2["expires"])

    def test_rebind_on_exit_failure_records_reason_and_old(self):
        b1 = bind("s1", "shop")
        b2 = rebind("s1", "exit unreachable")
        self.assertNotEqual(b1["proxy"], b2["proxy"])
        recs = [r for r in pin.records() if r["event"] == "rebind"]
        self.assertEqual(recs[-1]["reason"], "exit unreachable")
        self.assertEqual(recs[-1]["old_proxy"], b1["proxy"])
        self.assertEqual(recs[-1]["new_proxy"], b2["proxy"])

    def test_expired_binding_rebinds_on_next_bind(self):
        pin.configure("shop", ttl=0.05)
        b1 = bind("s1", "shop")
        time.sleep(0.07)
        b2 = bind("s1", "shop")
        self.assertGreater(b2["created"], b1["created"])
        recs = [r for r in pin.records() if r["event"] == "rebind"]
        self.assertEqual(recs[-1]["reason"], "expired")

    def test_site_isolation(self):
        b1 = bind("s1", "shop")
        b2 = bind("s1", "news")
        # 同一会话在不同站点是独立绑定条目
        self.assertIn(("s1", "shop"), pin._bindings)
        self.assertIn(("s1", "news"), pin._bindings)
        # 其他会话的绑定不受 rebind 影响
        b3 = bind("s2", "shop")
        rebind("s1", "exit failed")
        self.assertEqual(bind("s2", "shop")["proxy"], b3["proxy"])
        # 各站点独立计上限：shop 满员不影响 news
        pin.configure("shop", max_bindings=2)
        pin.configure("news", max_bindings=2)
        with self.assertRaises(pin.BindingLimitError):
            bind("s3", "shop")
        self.assertEqual(bind("s3", "news")["site"], "news")

    def test_switchback_after_cooldown(self):
        pin.configure("shop", cooldown=0.05,
                      proxies=["10.0.0.1:80", "10.0.0.2:80"])
        b1 = bind("s1", "shop")
        rebind("s1", "failed")          # 切到 10.0.0.2，10.0.0.1 冷却
        time.sleep(0.07)                # 等 10.0.0.1 冷却结束
        b3 = rebind("s1", "failed again")
        self.assertEqual(b3["proxy"], b1["proxy"])
        self.assertTrue(any(r["event"] == "switchback" for r in pin.records()))

    def test_site_limit_rejects_new_session(self):
        pin.configure("shop", max_bindings=1)
        bind("s1", "shop")
        with self.assertRaises(pin.BindingLimitError):
            bind("s2", "shop")
        # 站点隔离：news 不占 shop 的额度
        self.assertEqual(bind("s2", "news")["site"], "news")
