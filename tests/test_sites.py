import unittest

from crawlflow import Scheduler, SitePolicy


class SiteIsolationTest(unittest.TestCase):
    def test_per_site_concurrency_limit(self):
        s = Scheduler(sites={
            "slow.example": SitePolicy(max_concurrency=1),
            "fast.example": SitePolicy(max_concurrency=2),
        })
        s.add("https://slow.example/1")
        s.add("https://slow.example/2")
        s.add("https://fast.example/1")

        first = s.next()
        self.assertEqual(first.site, "slow.example")
        # slow 站点额度已满，跳过它取 fast 的任务：慢站点不拖住队列
        second = s.next()
        self.assertEqual(second.site, "fast.example")
        # 两个站点都满了
        self.assertIsNone(s.next())
        # 释放 slow 的槽位后可以继续
        s.success(first.url, "ok")
        third = s.next()
        self.assertEqual(third.url, "https://slow.example/2")

    def test_per_site_min_interval(self):
        s = Scheduler(sites={
            "api.example": SitePolicy(max_concurrency=5, min_interval=10.0),
        })
        s.add("https://api.example/1")
        s.add("https://api.example/2")

        self.assertIsNotNone(s.next(now=1000.0))
        # 并发没满，但距上次派发不足最小间隔
        self.assertIsNone(s.next(now=1005.0))
        self.assertGreater(s.site_retry_after("api.example", now=1005.0), 4.9)
        self.assertIsNotNone(s.next(now=1010.0))

    def test_default_policy_applies_to_unlisted_sites(self):
        s = Scheduler(default_policy=SitePolicy(max_concurrency=1))
        s.add("https://a.example/1")
        s.add("https://a.example/2")
        s.add("https://b.example/1")
        self.assertIsNotNone(s.next())
        self.assertEqual(s.next().site, "b.example")
        self.assertIsNone(s.next())

    def test_failure_releases_slot(self):
        s = Scheduler(sites={"x.example": SitePolicy(max_concurrency=1)})
        s.add("https://x.example/1")
        s.add("https://x.example/2")
        task = s.next()
        self.assertIsNone(s.next())
        s.failure(task.url)  # 兼容路径：立即重试
        again = s.next()
        self.assertEqual(again.url, "https://x.example/1")


if __name__ == "__main__":
    unittest.main()
