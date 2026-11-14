import unittest

from blockvote import allow, observe
from blockvote import block


class BlockTest(unittest.TestCase):
    def setUp(self):
        block._blocked.clear()

    def test_403_blocks(self):
        self.assertTrue(observe("shop", "403"))

    def test_captcha_ignored(self):
        self.assertFalse(observe("shop", "captcha"))

    def test_no_recovery(self):
        observe("shop", "403")
        observe("shop", "200")
        self.assertFalse(allow("shop"))


class VoteTest(unittest.TestCase):
    def setUp(self):
        block._blocked.clear()
        block._whitelist.clear()
        block._transitions.clear()
        block._probes.clear()

    def test_multi_signal_voting(self):
        self.assertFalse(observe("shop", "captcha"))   # 2票，仍正常
        self.assertTrue(observe("shop", "empty"))      # 累计3票，进观察态
        self.assertEqual(block.status("shop")["state"], "observing")
        self.assertEqual(block.status("shop")["votes"], 3)
        self.assertEqual(block.status("shop")["vote_detail"],
                         {"captcha": 1, "empty": 1})

    def test_signals_accumulate_per_site(self):
        observe("a", "redirect")   # 1票
        observe("b", "ratelimit")  # 2票
        self.assertTrue(allow("a"))
        self.assertTrue(allow("b"))
        observe("a", "ratelimit")  # a累计3票
        self.assertFalse(allow("a"))
        self.assertTrue(allow("b"))

    def test_threshold_fuse(self):
        observe("shop", "403")      # 3票，观察态
        observe("shop", "captcha")  # 5票，熔断
        self.assertEqual(block.status("shop")["state"], "blocked")
        kinds = [(t["from"], t["to"]) for t in block.transitions()]
        self.assertIn(("normal", "observing"), kinds)
        self.assertIn(("observing", "blocked"), kinds)

    def test_probe_recovery(self):
        observe("shop", "403", now=0.0)
        observe("shop", "403", now=0.0)   # 6票，熔断
        self.assertFalse(allow("shop"))
        observe("shop", "200", now=10.0)  # 探针1，熔断转观察
        self.assertFalse(allow("shop"))
        observe("shop", "200", now=10.5)  # 间隔不足，探针被忽略
        self.assertEqual(block.status("shop")["successes"], 1)
        observe("shop", "200", now=12.0)  # 探针2，连续成功解除
        self.assertTrue(allow("shop"))
        self.assertEqual(block.status("shop")["state"], "normal")
        self.assertEqual(block.status("shop")["votes"], 0)
        accepted = [p["accepted"] for p in block.probes()]
        self.assertEqual(accepted, [True, False, True])

    def test_probe_failure_refuses(self):
        observe("shop", "403", now=0.0)
        observe("shop", "captcha", now=0.0)  # 5票，熔断
        observe("shop", "200", now=5.0)      # 探针1
        observe("shop", "403", now=6.0)      # 失败信号，重新熔断
        self.assertEqual(block.status("shop")["state"], "blocked")
        self.assertEqual(block.status("shop")["successes"], 0)

    def test_whitelist_expiry(self):
        observe("shop", "403", now=0.0)
        block.whitelist("shop", ttl=60, now=1.0)
        self.assertTrue(allow("shop", now=2.0))          # 加白即放行
        self.assertFalse(observe("shop", "403", now=10.0))  # 期内不计票
        self.assertTrue(allow("shop", now=60.9))
        self.assertIsNone(block.status("shop", now=61.0)["whitelisted_until"])
        self.assertTrue(observe("shop", "403", now=61.0))   # 过期后重新计票
        self.assertFalse(allow("shop", now=61.0))
