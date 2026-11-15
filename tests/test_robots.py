import time
import unittest

from robotscache import RobotsCache

ROBOTS_DENY_PRIVATE = "User-agent: *\nDisallow: /private\n"
ROBOTS_ALLOW_ALL = "User-agent: *\nDisallow:\n"


def make_fetcher(routes, calls=None, fail=()):
    """routes: {(scheme, host, port): text 或 None}；fail: 抓取时抛错的 key 集合。"""

    def fetch(url):
        # url 形如 https://host:port/robots.txt
        scheme, rest = url.split("://")
        hostport = rest.split("/")[0]
        host, port = hostport.rsplit(":", 1)
        key = (scheme, host, int(port))
        if calls is not None:
            calls.append(key)
        if key in fail:
            raise OSError("connection refused")
        return routes.get(key)

    return fetch


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_cache(routes, ttl=60, fail=()):
    clock = FakeClock()
    calls = []
    cache = RobotsCache(fetcher=make_fetcher(routes, calls, fail), ttl=ttl, clock=clock)
    return cache, clock, calls


K = ("https", "shop.example.com", 443)
K_PARENT = ("https", "example.com", 443)


class CacheTest(unittest.TestCase):
    def test_second_request_hits_cache(self):
        cache, _, calls = make_cache({K: ROBOTS_DENY_PRIVATE})
        cache.allowed("https://shop.example.com/a")
        r = cache.allowed("https://shop.example.com/b")
        self.assertEqual(1, len([c for c in calls if c == K]))
        self.assertTrue(r["cached"])

    def test_disallow_with_reason(self):
        cache, _, _ = make_cache({K: ROBOTS_DENY_PRIVATE})
        r = cache.allowed("https://shop.example.com/private/x")
        self.assertFalse(r["allowed"])
        self.assertIn("disallowed", r["reason"])

    def test_port_isolated(self):
        k8080 = ("https", "shop.example.com", 8080)
        cache, _, _ = make_cache({K: ROBOTS_DENY_PRIVATE, k8080: ROBOTS_ALLOW_ALL})
        r = cache.allowed("https://shop.example.com:8080/private/x")
        self.assertTrue(r["allowed"])
        self.assertEqual(8080, r["port"])

    def test_expired_triggers_background_refresh_and_serves_stale(self):
        routes = {K: ROBOTS_DENY_PRIVATE}
        cache, clock, _ = make_cache(routes, ttl=60)
        cache.allowed("https://shop.example.com/a")
        routes[K] = ROBOTS_ALLOW_ALL  # 站点更新规则
        clock.advance(120)
        r = cache.allowed("https://shop.example.com/private/x")
        self.assertTrue(r["expired"])
        self.assertTrue(r["refreshed"])
        self.assertFalse(r["allowed"])  # 本次仍按旧规则
        for _ in range(100):  # 等后台刷新完成
            if cache.allowed("https://shop.example.com/private/x")["allowed"]:
                break
            time.sleep(0.02)
        self.assertTrue(cache.allowed("https://shop.example.com/private/x")["allowed"])

    def test_refresh_failure_keeps_old_rules_and_alerts(self):
        routes = {K: ROBOTS_DENY_PRIVATE}
        cache, clock, _ = make_cache(routes, ttl=60)
        cache.allowed("https://shop.example.com/a")
        clock.advance(120)
        del routes[K]  # 站点挂了
        # 把 fetcher 换成抛错，模拟刷新失败
        cache._fetch = make_fetcher({}, fail={K})
        r = cache.allowed("https://shop.example.com/private/x")
        self.assertTrue(r["refreshed"])
        for _ in range(100):
            if cache.alerts():
                break
            time.sleep(0.02)
        self.assertTrue(cache.alerts())
        self.assertEqual("kept-old-rules", cache.alerts()[0]["action"])
        self.assertFalse(cache.allowed("https://shop.example.com/private/x")["allowed"])

    def test_subdomain_uses_own_rules(self):
        k_sub = ("https", "sub.example.com", 443)
        cache, _, _ = make_cache({K_PARENT: ROBOTS_DENY_PRIVATE, k_sub: ROBOTS_ALLOW_ALL})
        r = cache.allowed("https://sub.example.com/private/x")
        self.assertTrue(r["allowed"])  # 子域有自己的规则，不被主域误伤
        self.assertEqual("own", r["source"])

    def test_subdomain_inherits_parent_when_missing(self):
        cache, _, _ = make_cache({K_PARENT: ROBOTS_DENY_PRIVATE})
        r = cache.allowed("https://sub.example.com/private/x")
        self.assertFalse(r["allowed"])
        self.assertEqual("inherited:example.com", r["source"])


class QueueSemanticsTest(unittest.TestCase):
    def test_queued_links_follow_old_rules(self):
        routes = {K: ROBOTS_ALLOW_ALL}
        cache, _, _ = make_cache(routes)
        ticket = cache.enqueue("https://shop.example.com/private/x")
        routes[K] = ROBOTS_DENY_PRIVATE  # 规则更新
        cache.refresh("shop.example.com")
        # 已入队链接按旧规则完成
        self.assertTrue(cache.allowed_queued(ticket)["allowed"])
        # 新请求按新规则
        self.assertFalse(cache.allowed("https://shop.example.com/private/x")["allowed"])


class ValidateUpdateTest(unittest.TestCase):
    URLS = ["https://shop.example.com/a", "https://shop.example.com/private/x"]

    def test_promote_when_rejection_scope_matches(self):
        routes = {K: ROBOTS_DENY_PRIVATE}
        cache, _, _ = make_cache(routes)
        cache.allowed("https://shop.example.com/a")
        routes[K] = "User-agent: *\nDisallow: /private\nCrawl-delay: 1\n"
        r = cache.validate_update("shop.example.com", self.URLS)
        self.assertTrue(r["promoted"])
        self.assertEqual(["https://shop.example.com/private/x"], r["rejected"])

    def test_keep_old_when_scope_differs(self):
        routes = {K: ROBOTS_DENY_PRIVATE}
        cache, _, _ = make_cache(routes)
        cache.allowed("https://shop.example.com/a")
        routes[K] = "User-agent: *\nDisallow: /\n"  # 拒绝范围扩大
        r = cache.validate_update("shop.example.com", self.URLS)
        self.assertFalse(r["promoted"])
        self.assertTrue(cache.alerts())
        # 旧规则仍在用
        self.assertTrue(cache.allowed("https://shop.example.com/a")["allowed"])

    def test_fetch_failure_keeps_old_and_alerts(self):
        routes = {K: ROBOTS_DENY_PRIVATE}
        cache, _, _ = make_cache(routes)
        cache.allowed("https://shop.example.com/a")
        cache._fetch = make_fetcher({}, fail={K})
        r = cache.validate_update("shop.example.com", self.URLS)
        self.assertFalse(r["promoted"])
        self.assertIn("error", r)
        self.assertTrue(cache.alerts())


if __name__ == "__main__":
    unittest.main()
