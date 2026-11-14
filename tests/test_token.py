import threading
import time
import unittest

from csrftoken import (
    FetchError,
    TokenError,
    TokenExpiredError,
    configure,
    consume,
    get,
    invalidate,
    refresh,
    reset,
    stats,
)


class TokenTest(unittest.TestCase):
    def test_get_returns_token(self):
        self.assertTrue(get("p1", 1)["token"])

    def test_step_echoed(self):
        self.assertEqual(get("p1", 2)["step"], 2)

    def test_invalidate(self):
        get("p1", 1)
        self.assertTrue(invalidate("p1"))


class SingleFlightTest(unittest.TestCase):
    def tearDown(self):
        reset()

    def test_concurrent_get_fetches_once(self):
        calls = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def fetcher(page, step):
            with lock:
                calls.append((page, step))
            time.sleep(0.05)  # 放大竞争窗口
            return "tok-%d" % len(calls)

        configure(fetcher=fetcher, ttl=60)
        results = []

        def worker():
            barrier.wait()
            results.append(get("page:form", 1)["token"])

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(calls), 1)               # 同域只抓一次
        self.assertEqual(set(results), {"tok-1"})     # 其余等待复用
        self.assertEqual(stats()["refetches"], 1)
        self.assertEqual(stats()["hits"], 7)


class ScopeTest(unittest.TestCase):
    def tearDown(self):
        reset()

    def test_steps_are_separate_scopes(self):
        calls = []

        def fetcher(page, step):
            calls.append(step)
            return "s%d-%d" % (step, len(calls))

        configure(fetcher=fetcher)
        t1 = get("p", 1)["token"]
        t2 = get("p", 2)["token"]
        self.assertNotEqual(t1, t2)
        self.assertEqual(get("p", 1)["token"], t1)  # 步骤 1 不被步骤 2 干扰
        self.assertEqual(sorted(calls), [1, 2])

    def test_forms_are_separate_scopes(self):
        configure(fetcher=lambda page, step: "tok@%s" % page)
        self.assertNotEqual(get("order:address", 1)["token"],
                            get("order:payment", 1)["token"])

    def test_invalidate_clears_all_steps_of_page(self):
        configure(fetcher=lambda page, step: "x")
        get("p", 1)
        get("p", 2)
        get("q", 1)
        invalidate("p")
        self.assertEqual(stats()["refetches"], 3)
        get("p", 1)  # p 的作用域已清，重取
        self.assertEqual(stats()["refetches"], 4)
        get("q", 1)  # q 不受影响，仍命中
        self.assertEqual(stats()["refetches"], 4)


class ConsumeTest(unittest.TestCase):
    def tearDown(self):
        reset()

    def test_consume_allows_single_winner(self):
        configure(fetcher=lambda page, step: "once")
        get("p", 1)
        barrier = threading.Barrier(5)
        results = []

        def worker():
            barrier.wait()
            results.append(consume("p", 1))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(results), [False, False, False, False, True])

    def test_get_after_consume_refetches(self):
        seq = []

        def fetcher(page, step):
            seq.append(1)
            return "tok-%d" % len(seq)

        configure(fetcher=fetcher)
        first = get("p", 1)["token"]
        self.assertTrue(consume("p", 1))
        self.assertFalse(consume("p", 1))
        second = get("p", 1)["token"]
        self.assertNotEqual(first, second)

    def test_consume_without_token_raises(self):
        configure()
        with self.assertRaises(TokenError):
            consume("nope", 1)

    def test_consume_expired_raises(self):
        now = [1000.0]
        configure(fetcher=lambda page, step: "x", ttl=10, clock=lambda: now[0])
        get("p", 1)
        now[0] += 20
        with self.assertRaises(TokenExpiredError):
            consume("p", 1)

    def test_expired_token_refetched_on_get(self):
        now = [1000.0]
        seq = []

        def fetcher(page, step):
            seq.append(1)
            return "tok-%d" % len(seq)

        configure(fetcher=fetcher, ttl=10, clock=lambda: now[0])
        t1 = get("p", 1)["token"]
        now[0] += 20
        t2 = get("p", 1)["token"]
        self.assertNotEqual(t1, t2)


class FallbackTest(unittest.TestCase):
    def tearDown(self):
        reset()

    def _flaky_pool(self):
        state = {"fail": False, "n": 0}

        def fetcher(page, step):
            if state["fail"]:
                raise RuntimeError("服务端 500")
            state["n"] += 1
            return "good-%d" % state["n"]

        configure(fetcher=fetcher, ttl=60)
        return state

    def test_refresh_failure_falls_back_and_alerts(self):
        state = self._flaky_pool()
        get("p", 1)                      # good-1
        current = refresh("p", 1)["token"]  # good-2，good-1 被顶替
        state["fail"] = True
        fell_back = refresh("p", 1)["token"]
        self.assertEqual(fell_back, current)  # 回退上一枚有效令牌
        s = stats()
        self.assertEqual(s["fallbacks"], 1)
        self.assertEqual(len(s["alerts"]), 1)
        self.assertIn("回退", s["alerts"][0])

    def test_fetch_failure_without_fallback_raises(self):
        state = self._flaky_pool()
        state["fail"] = True  # 从未抓到任何令牌，没有可回退的
        with self.assertRaises(FetchError):
            get("p", 1)

    def test_consumed_fallback_is_not_reused(self):
        state = self._flaky_pool()
        get("p", 1)
        refresh("p", 1)
        consume("p", 1)                  # 当前令牌被消费
        state["fail"] = True
        with self.assertRaises(FetchError):  # 回退令牌也已消费，无有效回退
            refresh("p", 1)


class ErrorPathTest(unittest.TestCase):
    def tearDown(self):
        reset()

    def test_bad_scope_args_raise(self):
        configure()
        with self.assertRaises(ValueError):
            get("", 1)
        with self.assertRaises(ValueError):
            get("p", 0)
        with self.assertRaises(ValueError):
            invalidate("")

    def test_fetcher_returning_garbage_raises(self):
        configure(fetcher=lambda page, step: "")
        with self.assertRaises(FetchError):
            get("p", 1)


if __name__ == "__main__":
    unittest.main()
