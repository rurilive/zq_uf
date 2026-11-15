"""模块级门面 API 冒烟测试（demo.py 使用的入口）。"""

import unittest

import dnscache2
from dnscache2 import begin_switch, invalidate, report, reset_default_cache, resolve


class FacadeTest(unittest.TestCase):
    def setUp(self):
        reset_default_cache()

    def test_resolve_report_基础门面(self):
        result = resolve("shop")
        self.assertEqual(result["ip"], "10.0.0.1")
        self.assertIn("ttl", result)
        self.assertIn("health", result)
        self.assertIn("refreshed", result)

        rep = report("shop", "10.0.0.1", True, 0.12)
        self.assertFalse(rep["refreshed"])
        self.assertEqual(rep["health"], 1.0)

    def test_未知站点回环地址(self):
        self.assertEqual(resolve("no-such-host")["ip"], "127.0.0.1")

    def test_门面暴露灰度与失效入口(self):
        self.assertTrue(callable(invalidate))
        self.assertTrue(callable(begin_switch))
        self.assertIn("DnsCache", dnscache2.__all__)
        self.assertIn("RolloutManager", dnscache2.__all__)


if __name__ == "__main__":
    unittest.main()
