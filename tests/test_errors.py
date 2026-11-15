import unittest
from datetime import datetime, timedelta, timezone

from failclass import (
    BLOCKED,
    ERROR,
    NOT_FOUND,
    TIMEOUT,
    FailureRouter,
    classify,
    policy,
    site_of,
)


class FakeClock:
    def __init__(self, start="2026-11-16T00:00:00+00:00"):
        self.now = datetime.fromisoformat(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class FakeResponse:
    def __init__(self, status_code, headers=None, url=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url


class ClassifyTest(unittest.TestCase):
    def test_timeout_error_is_timeout(self):
        result = classify(TimeoutError("read timed out"))
        self.assertEqual(result["reason"], TIMEOUT)
        self.assertEqual(result["evidence"]["message"], "read timed out")

    def test_string_timeout_is_timeout(self):
        self.assertEqual(classify("timeout")["reason"], TIMEOUT)

    def test_blocked_status_wins_over_message(self):
        response = {"status": 403, "headers": {"Server": "cloudflare"}}
        result = classify("timeout while reading", response)
        self.assertEqual(result["reason"], BLOCKED)

    def test_429_is_blocked(self):
        self.assertEqual(classify("", FakeResponse(429))["reason"], BLOCKED)

    def test_404_is_not_found(self):
        result = classify("", FakeResponse(404))
        self.assertEqual(result["reason"], NOT_FOUND)

    def test_410_is_not_found(self):
        self.assertEqual(classify("", {"status": 410})["reason"], NOT_FOUND)

    def test_504_is_timeout(self):
        self.assertEqual(classify("", FakeResponse(504))["reason"], TIMEOUT)

    def test_blocked_message(self):
        self.assertEqual(classify("访问被封禁, 需要验证码")["reason"], BLOCKED)

    def test_not_found_message(self):
        self.assertEqual(classify("页面不存在")["reason"], NOT_FOUND)

    def test_generic_error_is_error(self):
        self.assertEqual(classify("connection reset by peer")["reason"], ERROR)

    def test_evidence_has_status_headers_url(self):
        response = FakeResponse(
            403,
            {"Retry-After": "120", "X-RateLimit-Remaining": "0",
             "Set-Cookie": "session=secret"},
            "https://www.example.com/a",
        )
        evidence = classify("forbidden", response)["evidence"]
        self.assertEqual(evidence["status"], 403)
        self.assertEqual(evidence["url"], "https://www.example.com/a")
        self.assertEqual(evidence["headers"]["retry-after"], "120")
        self.assertEqual(evidence["retry_after"], 120)
        # 敏感头不进入证据。
        self.assertNotIn("set-cookie", evidence["headers"])

    def test_stable_reason_and_evidence_shape(self):
        for error, response in [
            ("timeout", None),
            ("", {"status": 404}),
            ("", {"status": 429}),
            (ValueError("bad"), None),
        ]:
            result = classify(error, response)
            self.assertEqual(set(result), {"reason", "evidence"})
            self.assertIn(
                result["reason"], {TIMEOUT, BLOCKED, NOT_FOUND, ERROR}
            )
            self.assertEqual(set(result["evidence"]),
                             {"reason", "status", "headers", "url", "message",
                              "error_type", "retry_after"})


class PolicyTest(unittest.TestCase):
    def test_actions_per_reason(self):
        self.assertEqual(policy(TIMEOUT)["action"], "retry")
        self.assertEqual(policy(BLOCKED)["action"], "switch_egress")
        self.assertEqual(policy(NOT_FOUND)["action"], "give_up")
        self.assertEqual(policy(ERROR)["action"], "retry")

    def test_timeout_waits(self):
        self.assertGreater(policy(TIMEOUT)["delay"], 0)

    def test_blocked_has_cooldown(self):
        self.assertGreater(policy(BLOCKED)["cooldown"], 0)

    def test_budget_is_site(self):
        for reason in (TIMEOUT, BLOCKED, ERROR):
            self.assertEqual(policy(reason)["budget"], "site")

    def test_policy_returns_copy(self):
        first = policy(ERROR)
        first["action"] = "mutated"
        self.assertEqual(policy(ERROR)["action"], "retry")

    def test_site_of(self):
        self.assertEqual(site_of("https://WWW.Example.com:8443/a"), "example.com")
        self.assertEqual(site_of("example.com/a"), "example.com")
        self.assertIsNone(site_of(""))


class RouterTest(unittest.TestCase):
    def make(self, budget=3, cooldown=300):
        clock = FakeClock()
        router = FailureRouter(budget=budget, blocked_cooldown=cooldown,
                               clock=clock)
        return router, clock

    def test_timeout_retries_with_delay(self):
        router, _ = self.make()
        result = router.handle("t1", TimeoutError("read timed out"),
                               url="https://a.com/1", request_id="r1")
        self.assertEqual(result["reason"], TIMEOUT)
        self.assertEqual(result["action"], "retry")
        self.assertGreater(result["delay"], 0)
        self.assertEqual(result["remaining"], 2)
        self.assertEqual(result["site"], "a.com")
        self.assertEqual(result["request_id"], "r1")

    def test_not_found_gives_up_without_spending_budget(self):
        router, _ = self.make()
        result = router.handle("t1", "", {"status": 404},
                               url="https://a.com/1", request_id="r1")
        self.assertEqual(result["action"], "give_up")
        self.assertTrue(result["review"])
        self.assertEqual(result["remaining"], 3)
        self.assertFalse(result["charged"])
        self.assertEqual(len(router.review_list("a.com")), 1)

    def test_blocked_switches_egress_and_cools_down(self):
        router, clock = self.make(cooldown=120)
        first = router.handle("t1", "", {"status": 403},
                              url="https://a.com/1", request_id="r1")
        self.assertEqual(first["action"], "switch_egress")
        self.assertEqual(first["cooldown"], 120)
        self.assertTrue(router.is_cooling("a.com"))

        # 冷却期内新的请求不应发出，gate 直接给出 wait。
        gate = router.gate("a.com", task="t2")
        self.assertEqual(gate["action"], "wait")
        self.assertTrue(gate["cooling"])

        # 即使调用方绕过 gate，冷却期内的失败也不计预算、不重复扣减。
        second = router.handle("t2", "boom", url="https://a.com/2",
                               request_id="r2")
        self.assertEqual(second["action"], "wait")
        self.assertTrue(second["cooling"])
        self.assertFalse(second["charged"])
        self.assertEqual(second["remaining"], 2)
        self.assertEqual(router.summary("a.com")["a.com"]["counts"], {BLOCKED: 1})

        clock.advance(121)
        self.assertFalse(router.is_cooling("a.com"))
        self.assertIsNone(router.gate("a.com"))
        third = router.handle("t2", TimeoutError("slow"),
                              url="https://a.com/2", request_id="r3")
        self.assertEqual(third["action"], "retry")
        self.assertEqual(third["remaining"], 1)

    def test_not_found_is_processed_during_cooldown(self):
        router, _ = self.make(cooldown=120)
        router.handle("t1", "", {"status": 403}, url="https://a.com/1")
        result = router.handle("t2", "", {"status": 404}, url="https://a.com/2")
        self.assertEqual(result["action"], "give_up")
        self.assertTrue(result["review"])
        self.assertEqual(len(router.review_list("a.com")), 1)

    def test_blocked_during_cooldown_extends_it(self):
        router, clock = self.make(cooldown=60)
        router.handle("t1", "", {"status": 403}, url="https://a.com/1")
        clock.advance(30)
        result = router.handle(
            "t2", "", {"status": 429, "headers": {"Retry-After": "600"}},
            url="https://a.com/2",
        )
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["cooldown"], 600)
        clock.advance(500)
        self.assertTrue(router.is_cooling("a.com"))
        clock.advance(101)
        self.assertFalse(router.is_cooling("a.com"))

    def test_blocked_respects_retry_after(self):
        router, _ = self.make(cooldown=60)
        result = router.handle(
            "t1", "", {"status": 429, "headers": {"Retry-After": "900"}},
            url="https://a.com/1",
        )
        self.assertEqual(result["cooldown"], 900)

    def test_absurd_retry_after_is_capped(self):
        router, _ = self.make(cooldown=60)
        result = router.handle(
            "t1", "", {"status": 429, "headers": {"Retry-After": "99999999"}},
            url="https://a.com/1",
        )
        self.assertEqual(result["cooldown"], 24 * 3600)

    def test_retry_after_http_date_is_parsed(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=120)
        header = when.strftime("%a, %d %b %Y %H:%M:%S GMT")
        evidence = classify("", {"status": 429, "headers": {"Retry-After": header}})
        self.assertGreater(evidence["evidence"]["retry_after"], 100)

    def test_exception_response_is_used(self):
        class HttpError(Exception):
            def __init__(self, message, response):
                super().__init__(message)
                self.response = response

        error = HttpError("boom", FakeResponse(404))
        self.assertEqual(classify(error)["reason"], NOT_FOUND)

    def test_www_and_bare_host_share_site(self):
        router, _ = self.make(budget=1)
        router.handle("t1", "boom", url="https://www.a.com/x")
        result = router.handle("t2", "boom", url="https://a.com/y")
        self.assertEqual(result["site"], "a.com")
        self.assertEqual(result["action"], "give_up")

    def test_request_id_is_auto_generated(self):
        router, _ = self.make()
        first = router.handle("t1", "boom", url="https://a.com/1")
        second = router.handle("t2", "boom", url="https://a.com/2")
        self.assertTrue(first["request_id"])
        self.assertNotEqual(first["request_id"], second["request_id"])
        self.assertEqual(len(router.history(site="a.com")), 2)

    def test_review_can_be_filtered_by_site(self):
        router, _ = self.make()
        router.handle("t1", "", {"status": 404}, url="https://a.com/1")
        router.handle("t2", "", {"status": 404}, url="https://b.com/1")
        self.assertEqual([i["task"] for i in router.review_list("a.com")], ["t1"])
        self.assertEqual(len(router.review_list()), 2)
        self.assertEqual(
            router.export_review(site="a.com", fmt="json").count('"task"'), 1
        )

    def test_timeout_backoff_grows_per_task(self):
        router, _ = self.make(budget=5)
        first = router.handle("t1", TimeoutError("slow"), url="https://a.com/1")
        second = router.handle("t1", TimeoutError("slow"), url="https://a.com/1")
        self.assertGreater(second["delay"], first["delay"])

    def test_budget_is_shared_across_tasks_on_same_site(self):
        router, _ = self.make(budget=3)
        actions = [
            router.handle(f"t{i}", "boom", url="https://a.com/x")["action"]
            for i in range(4)
        ]
        self.assertEqual(actions, ["retry", "retry", "retry", "give_up"])
        self.assertEqual(router.remaining("a.com"), 0)
        # 前三次失败消耗完站点的三次重试额度，超预算的第四次放弃且不再计费。
        self.assertEqual(router.summary("a.com")["a.com"]["used"], 3)

    def test_budget_is_per_site(self):
        router, _ = self.make(budget=1)
        router.handle("t1", "boom", url="https://a.com/x")
        other = router.handle("t2", "boom", url="https://b.com/x")
        self.assertEqual(other["action"], "retry")
        self.assertEqual(other["remaining"], 0)

    def test_give_up_enters_review_and_exports(self):
        router, _ = self.make(budget=1)
        router.handle("t1", "boom", url="https://a.com/x", request_id="r1")
        result = router.handle("t1", "", {"status": 503},
                               url="https://a.com/x", request_id="r2")
        self.assertEqual(result["action"], "give_up")
        self.assertTrue(result["review"])
        review = router.review_list("a.com")
        self.assertEqual(len(review), 1)
        self.assertEqual(review[0]["task"], "t1")

        csv_text = router.export_review(site="a.com", fmt="csv")
        self.assertIn("t1", csv_text)
        self.assertIn("a.com", csv_text)
        self.assertEqual(router.export_review(site="b.com", fmt="csv").count("\n"), 1)

    def test_summary_breaks_down_by_reason(self):
        router, clock = self.make(cooldown=1)
        router.handle("t1", TimeoutError("t"), url="https://a.com/1")
        router.handle("t2", "", {"status": 404}, url="https://a.com/2")
        summary = router.summary("a.com")["a.com"]
        self.assertEqual(summary["counts"], {TIMEOUT: 1, NOT_FOUND: 1})
        self.assertEqual(summary["total_failures"], 2)
        self.assertEqual(summary["review"], 1)
        clock.advance(86400 * 2)
        router.handle("t3", "boom", url="https://b.com/1")
        # 跨天后站点预算重置，历史构成仍保留。
        self.assertEqual(router.summary("a.com")["a.com"]["used"], 0)
        self.assertEqual(router.summary("a.com")["a.com"]["counts"], {})
        self.assertEqual(
            router.summary("a.com")["a.com"]["lifetime"],
            {TIMEOUT: 1, NOT_FOUND: 1},
        )

    def test_history_is_traceable(self):
        router, _ = self.make(cooldown=10)
        router.handle("t1", "", {"status": 403}, url="https://a.com/1",
                      request_id="req-1")
        entries = router.history(site="a.com", reason=BLOCKED)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["request_id"], "req-1")
        self.assertEqual(entries[0]["evidence"]["status"], 403)
        self.assertEqual(entries[0]["task"], "t1")

    def test_requeue_grants_free_retry(self):
        router, _ = self.make(budget=1)
        router.handle("t1", "boom", url="https://a.com/x")
        router.handle("t1", "boom", url="https://a.com/x")
        self.assertEqual(router.remaining("a.com"), 0)
        self.assertEqual(router.review_list("a.com")[0]["task"], "t1")

        entry = router.requeue("t1")
        self.assertEqual(entry["status"], "requeued")
        self.assertEqual(router.review_list(), [])

        result = router.handle("t1", "boom", url="https://a.com/x")
        self.assertEqual(result["action"], "retry")
        self.assertFalse(result["charged"])
        self.assertEqual(router.remaining("a.com"), 0)

        # 免费额度只有一次，之后继续按站点预算处理。
        again = router.handle("t1", "boom", url="https://a.com/x")
        self.assertEqual(again["action"], "give_up")

    def test_requeue_unknown_task_returns_none(self):
        router, _ = self.make()
        self.assertIsNone(router.requeue("nope"))

    def test_multiple_tasks_same_site_share_attempts(self):
        router, _ = self.make()
        router.handle("t1", "boom", url="https://a.com/1")
        router.handle("t2", "boom", url="https://a.com/2")
        router.handle("t3", "boom", url="https://a.com/3")
        result = router.handle("t4", "boom", url="https://a.com/4")
        self.assertEqual(result["action"], "give_up")
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(len(router.review_list("a.com")), 1)


if __name__ == "__main__":
    unittest.main()
