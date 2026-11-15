import json
import os
import tempfile
import unittest

from failclass import (
    BLOCKED,
    ERROR,
    NOT_FOUND,
    REASONS,
    TIMEOUT,
    FailureGovernor,
    classify,
    normalize_reason,
    policy,
)


class FakeResponse:
    """最小的 requests.Response 替身。"""

    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class ClassifyTest(unittest.TestCase):
    def test_timeout_is_not_generic_error(self):
        self.assertEqual(classify("timeout")["reason"], TIMEOUT)
        self.assertEqual(classify(TimeoutError("boom"))["reason"], TIMEOUT)
        self.assertEqual(classify("read timed out")["reason"], TIMEOUT)

    def test_blocked_variants(self):
        self.assertEqual(classify("HTTP 403 banned")["reason"], BLOCKED)
        self.assertEqual(classify("429 too many requests")["reason"], BLOCKED)
        self.assertEqual(
            classify(None, FakeResponse(429, {"Retry-After": "60"}))["reason"],
            BLOCKED,
        )
        self.assertEqual(
            classify("captcha required")["reason"], BLOCKED
        )

    def test_not_found_from_status_and_text(self):
        self.assertEqual(classify(None, {"status": 404})["reason"], NOT_FOUND)
        self.assertEqual(classify("page 410 gone")["reason"], NOT_FOUND)
        self.assertEqual(classify("页面不存在")["reason"], NOT_FOUND)

    def test_status_beats_message(self):
        # 404 的响应体可能写着 "timeout"，但状态码说了算。
        result = classify("timeout while reading", FakeResponse(404))
        self.assertEqual(result["reason"], NOT_FOUND)

    def test_unknown_falls_back_to_error(self):
        self.assertEqual(classify(ValueError("boom"))["reason"], ERROR)
        self.assertEqual(classify("some weird failure")["reason"], ERROR)

    def test_evidence_carries_status_and_headers(self):
        result = classify(
            "rate limited",
            FakeResponse(429, {"Retry-After": "120", "X-Request-Id": "abc"}),
            request_id="req-7",
            url="https://shop.example/p/1",
        )
        self.assertEqual(result["status"], 429)
        self.assertEqual(result["headers"]["Retry-After"], "120")
        evidence = result["evidence"]
        self.assertEqual(evidence["status"], 429)
        self.assertEqual(evidence["headers"]["X-Request-Id"], "abc")
        self.assertEqual(evidence["request_id"], "req-7")
        self.assertEqual(evidence["url"], "https://shop.example/p/1")

    def test_exception_response_is_used(self):
        class Boom(Exception):
            response = FakeResponse(403, {"CF-Mitigated": "challenge"})

        self.assertEqual(classify(Boom("x"))["reason"], BLOCKED)

    def test_normalize_reason_aliases(self):
        self.assertEqual(normalize_reason("BAN"), BLOCKED)
        self.assertEqual(normalize_reason("not-found"), NOT_FOUND)
        self.assertEqual(normalize_reason(None), ERROR)
        self.assertEqual(normalize_reason({"reason": "timeout"}), TIMEOUT)
        for reason in REASONS:
            self.assertEqual(normalize_reason(reason), reason)


class PolicyTest(unittest.TestCase):
    def test_timeout_waits_before_retry(self):
        rule = policy(TIMEOUT)
        self.assertEqual(rule["action"], "retry")
        self.assertGreater(rule["delay"], 0)

    def test_blocked_switches_egress_and_cools_down(self):
        rule = policy(BLOCKED)
        self.assertEqual(rule["action"], "switch_egress")
        self.assertTrue(rule["switch_egress"])
        self.assertGreater(rule["cooldown"], 0)

    def test_not_found_abandons(self):
        rule = policy(NOT_FOUND)
        self.assertEqual(rule["action"], "abandon")
        self.assertFalse(rule["switch_egress"])

    def test_budget_is_site_scoped(self):
        for reason in REASONS:
            self.assertEqual(policy(reason)["budget"], "site")

    def test_policy_accepts_classification(self):
        self.assertEqual(policy(classify("timeout"))["action"], "retry")

    def test_policy_returns_copy(self):
        policy(TIMEOUT)["delay"] = 999
        self.assertNotEqual(policy(TIMEOUT)["delay"], 999)


class GovernorTest(unittest.TestCase):
    def setUp(self):
        self.gov = FailureGovernor(max_retries_per_site=3, blocked_cooldown=60)

    def test_budget_is_shared_per_site(self):
        r1 = self.gov.record_failure("a.test", "timeout", task_id="t1")
        r2 = self.gov.record_failure("a.test", "timeout", task_id="t2")
        self.assertEqual((r1["remaining"], r2["remaining"]), (2, 1))
        self.assertEqual(r1["budget_scope"], "site")

    def test_different_sites_do_not_share_budget(self):
        self.gov.record_failure("a.test", "timeout", task_id="t1")
        other = self.gov.record_failure("b.test", "timeout", task_id="t2")
        self.assertEqual(other["remaining"], 2)

    def test_budget_exhaustion_abandons(self):
        for i in range(3):
            decision = self.gov.record_failure("a.test", "timeout", task_id=f"t{i}")
            self.assertEqual(decision["action"], "retry")
        final = self.gov.record_failure("a.test", "timeout", task_id="t9")
        self.assertEqual(final["action"], "abandon")
        self.assertEqual(final["remaining"], 0)

    def test_not_found_abandons_without_consuming_budget(self):
        decision = self.gov.record_failure(
            "a.test", "not found", {"status": 404}, task_id="t1"
        )
        self.assertEqual(decision["action"], "abandon")
        self.assertFalse(decision["retryable"])
        self.gov.record_failure("a.test", "timeout", task_id="t2")
        self.assertEqual(self.gov.site_status("a.test")["retries_used"], 1)

    def test_blocked_switches_egress_and_cools_down(self):
        decision = self.gov.record_failure(
            "a.test", "429", {"status": 429}, task_id="t1"
        )
        self.assertEqual(decision["action"], "switch_egress")
        self.assertTrue(decision["switch_egress"])
        self.assertEqual(decision["cooldown_until"] is not None, True)
        status = self.gov.site_status("a.test")
        self.assertFalse(status["available"])
        self.assertGreater(status["cooldown_seconds"], 0)

    def test_during_cooldown_other_reasons_wait(self):
        self.gov.record_failure("a.test", "429", {"status": 429}, task_id="t1")
        decision = self.gov.record_failure("a.test", "timeout", task_id="t2")
        self.assertEqual(decision["action"], "wait")
        self.assertGreater(decision["delay"], 0)

    def test_cooldown_expires_automatically(self):
        clock = {"now": 1000.0}
        gov = FailureGovernor(
            max_retries_per_site=5,
            blocked_cooldown=30,
            clock=lambda: clock["now"],
        )
        gov.record_failure("a.test", "429", {"status": 429}, task_id="t1")
        self.assertFalse(gov.site_status("a.test")["available"])
        clock["now"] += 31
        status = gov.site_status("a.test")
        self.assertTrue(status["available"])
        self.assertEqual(status["cooldown_seconds"], 0.0)
        # 冷却结束后又能正常处理，无需改配置。
        decision = gov.record_failure("a.test", "timeout", task_id="t2")
        self.assertEqual(decision["action"], "retry")

    def test_success_clears_cooldown(self):
        self.gov.record_failure("a.test", "429", {"status": 429}, task_id="t1")
        self.gov.record_success("a.test", task_id="t2")
        self.assertTrue(self.gov.site_status("a.test")["available"])

    def test_review_queue_only_holds_abandoned(self):
        self.gov.record_failure("a.test", "timeout", task_id="t1")
        self.gov.record_failure("a.test", "not found", {"status": 404}, task_id="t2")
        review = self.gov.review_tasks("a.test")
        self.assertEqual([r["task_id"] for r in review], ["t2"])
        self.assertEqual(review[0]["reason"], NOT_FOUND)

    def test_review_dedupes_same_task(self):
        for _ in range(2):
            self.gov.record_failure("a.test", "404", {"status": 404}, task_id="t1")
        self.assertEqual(len(self.gov.review_tasks()), 1)

    def test_requeue_does_not_consume_budget(self):
        for i in range(3):
            self.gov.record_failure("a.test", "timeout", task_id=f"t{i}")
        self.gov.record_failure("a.test", "timeout", task_id="stuck")
        used_before = self.gov.site_status("a.test")["retries_used"]
        result = self.gov.requeue("stuck")
        self.assertEqual(result["action"], "requeue")
        self.assertFalse(result["counted"])
        self.assertEqual(self.gov.site_status("a.test")["retries_used"], used_before)
        self.assertIsNone(self.gov.requeue("stuck"))

    def test_failure_breakdown_per_site(self):
        self.gov.record_failure("a.test", "timeout", task_id="t1")
        self.gov.record_failure("a.test", "timeout", task_id="t2")
        self.gov.record_failure("a.test", "404", {"status": 404}, task_id="t3")
        self.gov.record_failure("b.test", "403", {"status": 403}, task_id="t4")
        breakdown = self.gov.failure_breakdown()
        self.assertEqual(breakdown["a.test"][TIMEOUT], 2)
        self.assertEqual(breakdown["a.test"][NOT_FOUND], 1)
        self.assertEqual(breakdown["b.test"][BLOCKED], 1)
        self.assertEqual(self.gov.failure_breakdown("a.test")[BLOCKED], 0)

    def test_history_traces_request(self):
        self.gov.record_failure(
            "a.test",
            "429",
            {"status": 429, "headers": {"X-Request-Id": "rid-1"}},
            task_id="t1",
            request_id="rid-1",
        )
        trail = self.gov.history(request_id="rid-1")
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0]["evidence"]["headers"]["X-Request-Id"], "rid-1")
        self.assertEqual(trail[0]["reason"], BLOCKED)
        self.assertEqual(len(self.gov.history(site="a.test")), 1)

    def test_export_review_by_site(self):
        self.gov.record_failure("a.test", "404", {"status": 404}, task_id="a1")
        self.gov.record_failure("b.test", "410", {"status": 410}, task_id="b1")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "a.test.json")
            info = self.gov.export_review(path, site="a.test")
            self.assertEqual((info["count"], info["site"]), (1, "a.test"))
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["items"][0]["task_id"], "a1")

    def test_review_item_has_actionable_evidence(self):
        decision = self.gov.record_failure(
            "a.test",
            "banned",
            {"status": 403, "headers": {"Server": "cloudflare"}},
            task_id="t1",
            request_id="rid-9",
            url="https://a.test/x",
        )
        self.assertEqual(
            set(decision) >= {"reason", "action", "remaining", "evidence"}, True
        )
        self.assertEqual(decision["evidence"]["status"], 403)
        self.assertEqual(decision["evidence"]["request_id"], "rid-9")

    def test_negative_config_rejected(self):
        with self.assertRaises(ValueError):
            FailureGovernor(max_retries_per_site=-1)
        with self.assertRaises(ValueError):
            FailureGovernor(blocked_cooldown=-1)

    def test_blocked_cools_down_even_when_budget_exhausted(self):
        gov = FailureGovernor(max_retries_per_site=1, blocked_cooldown=60)
        gov.record_failure("a.test", "timeout", task_id="t1")
        decision = gov.record_failure("a.test", "429", {"status": 429}, task_id="t2")
        self.assertEqual(decision["action"], "abandon")
        # 即使转复核，站点也要停一段时间，避免继续打。
        self.assertFalse(gov.site_status("a.test")["available"])

    def test_query_unknown_site_has_no_side_effect(self):
        self.assertEqual(self.gov.failure_breakdown(), {})
        status = self.gov.site_status("never-seen.test")
        self.assertTrue(status["available"])
        self.assertEqual(status["retries_remaining"], 3)
        self.assertEqual(self.gov.failure_breakdown(), {})

    def test_returned_evidence_is_defensive_copy(self):
        decision = self.gov.record_failure(
            "a.test", "429", {"status": 429, "headers": {"X": "1"}}, task_id="t1"
        )
        decision["evidence"]["headers"]["X"] = "tampered"
        trail = self.gov.history(request_id=None)
        self.assertEqual(trail[0]["evidence"]["headers"]["X"], "1")

    def test_daily_budget_resets_but_cooldown_can_survive(self):
        clock = {"now": 1_000_000.0}
        gov = FailureGovernor(
            max_retries_per_site=2,
            blocked_cooldown=60,
            clock=lambda: clock["now"],
        )
        gov.record_failure("a.test", "timeout", task_id="t1")
        gov.record_failure("a.test", "timeout", task_id="t2")
        self.assertEqual(gov.site_status("a.test")["retries_remaining"], 0)
        clock["now"] += 24 * 3600  # 次日
        self.assertEqual(gov.site_status("a.test")["retries_remaining"], 2)
        self.assertEqual(gov.failure_breakdown("a.test")[TIMEOUT], 0)

    def test_status_report_names_the_site(self):
        self.gov.record_failure("a.test", "timeout", task_id="t1")
        self.assertEqual(self.gov.site_status("a.test")["site"], "a.test")


if __name__ == "__main__":
    unittest.main()
