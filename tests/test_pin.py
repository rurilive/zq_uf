import unittest

from sessionpin2 import pin


class PinTest(unittest.TestCase):
    def setUp(self):
        pin.reset()

    def test_bind_pins_within_ttl(self):
        b1 = pin.bind("s1", "shop", now=1000)
        b2 = pin.bind("s1", "shop", now=1000 + pin.TTL_SECONDS - 1)
        self.assertEqual(b1["proxy"], b2["proxy"])
        self.assertFalse(b2["rebound"])
        self.assertGreater(b2["expires_in"], 0)

    def test_expiry_triggers_rebind_with_reason(self):
        pin.bind("s1", "shop", now=1000)
        b = pin.bind("s1", "shop", now=1000 + pin.TTL_SECONDS + 1)
        self.assertTrue(b["rebound"])
        self.assertEqual(b["reason"], "expired")

    def test_failed_proxy_triggers_rebind_and_cooldown(self):
        b1 = pin.bind("s1", "shop", now=1000)
        pin.mark_failed(b1["proxy"])
        b2 = pin.bind("s1", "shop", now=1010)
        self.assertTrue(b2["rebound"])
        self.assertEqual(b2["reason"], "proxy-failed")
        self.assertNotEqual(b2["proxy"], b1["proxy"])
        self.assertEqual(b2["previous"], b1["proxy"])
        self.assertEqual(b2["cooldown_until"], 1010 + pin.COOLDOWN_SECONDS)
        self.assertIn("冷却中", b2["hint"])

    def test_cooldown_blocks_then_allows_switch_back(self):
        b1 = pin.bind("s1", "shop", now=1000)  # proxy-a
        pin.mark_failed(b1["proxy"])
        pin.bind("s1", "shop", now=1010)       # 换到别的出口，旧出口冷却
        pin.mark_recovered(b1["proxy"])
        # 冷却未结束：换绑不回切
        b = pin.rebind("s1", "shop", "manual", now=1010 + pin.COOLDOWN_SECONDS - 1)
        self.assertNotEqual(b["proxy"], b1["proxy"])
        # 冷却结束：下一次换绑触发回切
        b = pin.rebind("s1", "shop", "manual", now=1010 + pin.COOLDOWN_SECONDS + 1)
        self.assertEqual(b["proxy"], b1["proxy"])

    def test_sites_do_not_share_bindings(self):
        pin.bind("s1", "site-a", now=1000)
        pin.bind("s1", "site-b", now=1000)
        pin.rebind("s1", "site-a", "failed", now=1010)
        views = {v["site"]: v for v in pin.query("s1", now=1020)}
        self.assertEqual(views["site-b"]["reason"], "new")
        self.assertEqual(views["site-a"]["reason"], "failed")

    def test_lru_eviction_over_limit(self):
        old_max = pin.MAX_BINDINGS
        pin.MAX_BINDINGS = 3
        try:
            for i in range(3):
                pin.bind("s%d" % i, "shop", now=1000)
            pin.bind("s0", "shop", now=1001)   # s0 变最新，s1 最久未用
            pin.bind("s3", "shop", now=1002)   # 超限，回收 s1
            self.assertEqual(pin.query("s1", now=1003), [])
            self.assertEqual(len(pin.query("s0", now=1003)), 1)
        finally:
            pin.MAX_BINDINGS = old_max

    def test_query_by_session(self):
        pin.bind("s1", "site-a", now=1000)
        pin.bind("s1", "site-b", now=1000)
        pin.bind("s2", "site-a", now=1000)
        self.assertEqual(len(pin.query("s1", now=1001)), 2)
        self.assertEqual(len(pin.query("s2", now=1001)), 1)

    def test_export_records_history(self):
        pin.bind("s1", "shop", now=1000)
        pin.rebind("s1", "shop", "failed", now=1010)
        data = pin.export()
        self.assertEqual(len(data["bindings"]), 1)
        self.assertEqual(len(data["history"]), 2)
        self.assertEqual(data["history"][-1]["reason"], "failed")
        self.assertIn("from", data["history"][-1])

    def test_rollout_gates_pinning(self):
        pin.set_rollout(["s1"])
        off = pin.bind("s2", "shop", now=1000)
        self.assertEqual(off["expires_in"], 0)
        self.assertIn("灰度未覆盖", off["hint"])
        on = pin.bind("s1", "shop", now=1000)
        self.assertGreater(on["expires_in"], 0)
        pin.set_rollout(None)


if __name__ == "__main__":
    unittest.main()
