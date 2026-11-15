import time
import unittest

import sessionpin2
from sessionpin2 import bind, rebind


class PinTest(unittest.TestCase):
    def test_bind_uses_first_proxy(self):
        self.assertEqual(bind("s1", "a")["proxy"], "proxy-a")

    def test_bind_has_no_expiry(self):
        self.assertEqual(bind("s2", "a")["expires_in"], 0)

    def test_rebind_records_reason(self):
        self.assertEqual(rebind("s1", "failed")["reason"], "failed")


class StickyBindingTest(unittest.TestCase):
    def setUp(self):
        sessionpin2.reset()

    def tearDown(self):
        sessionpin2.reset()

    def test_bind_sticks_within_ttl(self):
        sessionpin2.configure(ttl=60)
        first = bind("s10", "shop")
        second = bind("s10", "shop")
        self.assertEqual(first["proxy"], second["proxy"])
        self.assertGreater(second["expires_in"], 0)
        kinds = [e["event"] for e in sessionpin2.events()]
        self.assertEqual(kinds, ["bind"])

    def test_rebind_on_failure_records_old_proxy(self):
        sessionpin2.configure(ttl=60, cooldown=30)
        bind("s11", "shop")
        out = rebind("s11", "proxy failed")
        self.assertEqual(out["old_proxy"], "proxy-a")
        self.assertEqual(out["proxy"], "proxy-b")
        self.assertEqual(out["cooldown"], 30)
        # 重绑后保持在新出口
        self.assertEqual(bind("s11", "shop")["proxy"], "proxy-b")

    def test_rebind_requires_valid_session(self):
        sessionpin2.configure(ttl=60)
        self.assertRaises(ValueError, rebind, "ghost", "failed")

    def test_expired_binding_rebinds(self):
        sessionpin2.configure(ttl=0.1)
        bind("s12", "shop")
        time.sleep(0.2)
        bind("s12", "shop")
        kinds = [e["event"] for e in sessionpin2.events()]
        self.assertEqual(kinds, ["bind", "bind"])

    def test_switchback_after_cooldown(self):
        sessionpin2.configure(ttl=0.1, cooldown=0.1)
        bind("s13", "shop")
        rebind("s13", "proxy failed")
        self.assertEqual(bind("s13", "shop")["proxy"], "proxy-b")
        time.sleep(0.2)  # 绑定过期且冷却结束
        self.assertEqual(bind("s13", "shop")["proxy"], "proxy-a")
        backs = [e for e in sessionpin2.events() if e["event"] == "switchback"]
        self.assertEqual(len(backs), 1)
        self.assertEqual(backs[0]["proxy"], "proxy-a")
        self.assertEqual(backs[0]["cooldown"], 0.1)


class SiteIsolationTest(unittest.TestCase):
    def setUp(self):
        sessionpin2.reset()

    def tearDown(self):
        sessionpin2.reset()

    def test_sites_are_isolated(self):
        sessionpin2.configure(ttl=60)
        bind("s20", "shop")
        bind("s20", "news")
        # 同一会话在两个站点各有独立绑定
        kinds = [e["event"] for e in sessionpin2.events()]
        self.assertEqual(kinds, ["bind", "bind"])
        # 两个站点的绑定都在有效期内，不新增绑定
        bind("s20", "shop")
        bind("s20", "news")
        self.assertEqual(len(sessionpin2.events()), 2)

    def test_site_limit_recycles_lru(self):
        sessionpin2.configure(ttl=60, site_limits={"shop": 2})
        bind("s21", "shop")
        bind("s22", "shop")
        bind("s23", "news")
        bind("s24", "shop")  # shop 超限，回收最久未用的 s21
        before = len(sessionpin2.events())
        bind("s23", "news")  # news 不受影响，绑定仍在
        self.assertEqual(len(sessionpin2.events()), before)
        bind("s21", "shop")  # s21 已被回收，重新绑定
        self.assertEqual(len(sessionpin2.events()), before + 1)

    def test_global_limit_recycles_lru(self):
        sessionpin2.configure(ttl=60, max_bindings=2)
        bind("s25", "a")
        bind("s26", "a")
        bind("s27", "a")  # 超限，回收最久未用的 s25
        before = len(sessionpin2.events())
        bind("s26", "a")  # 仍在有效期内
        self.assertEqual(len(sessionpin2.events()), before)
        bind("s25", "a")  # 已被回收，重新绑定
        self.assertEqual(len(sessionpin2.events()), before + 1)
