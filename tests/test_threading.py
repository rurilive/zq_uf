"""真实线程池执行器下的并发与单飞验证。"""

import threading
import time
import unittest

from dnscache2 import (
    DnsCache,
    ResolutionError,
    StaticResolver,
    ThreadRefreshExecutor,
)


class CountingResolver:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def resolve(self, host):
        with self.lock:
            self.calls.append(host)
        return StaticResolver({"shop": [("10.0.0.1", "a")]}).resolve(host)


class SlowResolver:
    def __init__(self, delay=0.1):
        self.delay = delay
        self.calls = 0
        self.lock = threading.Lock()

    def resolve(self, host):
        with self.lock:
            self.calls += 1
        time.sleep(self.delay)
        return StaticResolver({"shop": [("10.0.0.1", "a")]}).resolve(host)


class InitiallyFailingResolver:
    """前 fail_times 次抛异常，之后恢复；验证冷路径失败也会唤醒等待者。"""

    def __init__(self, fail_times=2):
        self.fail_times = fail_times
        self.calls = 0
        self.lock = threading.Lock()

    def resolve(self, host):
        with self.lock:
            self.calls += 1
            n = self.calls
        time.sleep(0.02)
        if n <= self.fail_times:
            raise ResolutionError("boom")
        return StaticResolver({"shop": [("10.0.0.1", "a")]}).resolve(host)


class ThreadingTest(unittest.TestCase):
    def test_冷路径单飞_并发首请求只解析一次(self):
        resolver = SlowResolver(delay=0.1)
        cache = DnsCache(resolver, ttl=30.0,
                         executor=ThreadRefreshExecutor(max_workers=2))
        try:
            results = []
            errors = []

            def worker():
                try:
                    results.append(cache.resolve("shop"))
                except Exception as exc:  # pragma: no cover - 测试守卫
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            self.assertEqual(errors, [])
            self.assertEqual(len(results), 10)
            self.assertEqual(resolver.calls, 1)  # 单飞：后端只被打一次
        finally:
            cache.shutdown()

    def test_后台刷新在工作线程完成且结果可观测(self):
        resolver = CountingResolver()
        cache = DnsCache(resolver, ttl=0.05,
                         executor=ThreadRefreshExecutor(max_workers=2))
        try:
            cache.resolve("shop")
            # 等待 TTL 确实过期，再并发请求，均应拿到旧地址而不阻塞
            time.sleep(0.1)
            results = []

            def worker():
                results.append(cache.resolve("shop"))

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            self.assertEqual(len(results), 8)
            self.assertTrue(all(r["ip"] == "10.0.0.1" for r in results))
            cache.wait_for_refreshes(timeout=5)
            # 冷解析 1 次 + 过期后至少 1 次后台刷新
            self.assertGreaterEqual(len(resolver.calls), 2)
        finally:
            cache.shutdown()

    def test_冷解析失败也会唤醒等待者并最终成功(self):
        resolver = InitiallyFailingResolver(fail_times=2)
        cache = DnsCache(resolver, ttl=30.0,
                         executor=ThreadRefreshExecutor(max_workers=2))
        try:
            outcomes = []

            def worker():
                for _ in range(20):
                    try:
                        outcomes.append(cache.resolve("shop")["ip"])
                        return
                    except ResolutionError:
                        time.sleep(0.01)

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)

            self.assertEqual(len(outcomes), 6)  # 所有等待者都拿到了结果
            self.assertTrue(all(ip == "10.0.0.1" for ip in outcomes))
            self.assertFalse(any(t.is_alive() for t in threads))  # 无人挂死
        finally:
            cache.shutdown()


if __name__ == "__main__":
    unittest.main()
