import unittest
import time
import urllib.request
from urllib.parse import parse_qsl, urlsplit

import ssrfverify
from ssrfverify import (
    EmbeddedReceiver,
    HttpResponse,
    ReceiverUnavailable,
    callbacks,
    check,
    configure,
    report,
    reset,
    shutdown,
)


class StubReceiver(ssrfverify.BaseReceiver):
    """可控的接收端：测试里手动注入回连。"""

    base_url = "http://cb.test"

    def __init__(self, up=True):
        self.up = up
        self._records = []

    def available(self):
        return self.up

    def callbacks(self):
        return list(self._records)

    def add(self, token, source="10.9.9.9", headers=None):
        from ssrfverify.receiver import Callback

        self._records.append(
            Callback(
                token=token,
                at=time.time(),
                source_address=source,
                headers=headers or {"Host": "cb.test"},
            )
        )


def payload_of(target_url, param="id"):
    """从探测地址里取出注入的 payload。"""
    return dict(parse_qsl(urlsplit(target_url).query))[param]


def transport(body="ok", status=200, on_payload=None):
    """模拟目标：on_payload(payload) 用来模拟目标真的去请求 payload。"""

    def send(target_url, timeout):
        if on_payload is not None:
            on_payload(payload_of(target_url))
        return HttpResponse(status=status, headers={"Content-Type": "text/plain"}, body=body)

    return send


class CheckTest(unittest.TestCase):
    def setUp(self):
        shutdown()
        configure(receiver=None, timeout=0.3, transport=None)
        reset()

    def tearDown(self):
        shutdown()

    def _receiver(self):
        receiver = StubReceiver()
        configure(receiver=receiver)
        return receiver

    def test_callback_means_hit(self):
        receiver = self._receiver()
        got = check(
            "http://10.0.0.5/item?id=1",
            "id",
            transport=transport(on_payload=lambda payload: receiver.add(payload.rsplit("/", 1)[-1])),
        )
        self.assertEqual(got["status"], "hit")
        self.assertTrue(got["hit"])
        self.assertIsNotNone(got["callback_at"])
        self.assertGreaterEqual(got["callback_elapsed"], 0)
        self.assertEqual(got["source_address"], "10.9.9.9")

    def test_no_callback_but_abnormal_response_is_suspicious(self):
        self._receiver()
        got = check(
            "http://10.0.0.5/item?id=2",
            "id",
            transport=transport(body="root:x:0:0"),
        )
        self.assertEqual(got["status"], "suspicious")
        self.assertFalse(got["hit"])
        self.assertEqual(got["reason"], "internal_marker:root:x:0:0")
        # 原始响应保留下来供复查
        self.assertEqual(got["response"]["body"], "root:x:0:0")
        self.assertEqual(got["response"]["status"], 200)

    def test_reflected_payload_is_suspicious_not_hit(self):
        self._receiver()

        def send(target_url, timeout):
            return HttpResponse(status=200, body=target_url)

        got = check("http://10.0.0.5/item?id=3", "id", transport=send)
        self.assertEqual(got["status"], "suspicious")
        self.assertEqual(got["reason"], "payload_echoed")

    def test_plain_response_is_clean(self):
        self._receiver()
        got = check("http://10.0.0.5/item?id=4", "id", transport=transport(body="not found"))
        self.assertEqual(got["status"], "clean")
        self.assertFalse(got["hit"])

    def test_transport_error_is_suspicious(self):
        self._receiver()

        def boom(url, timeout):
            raise ConnectionError("blocked by egress filter")

        got = check("http://10.0.0.5/item?id=5", "id", transport=boom)
        self.assertEqual(got["status"], "suspicious")
        self.assertEqual(got["reason"], "transport_error")
        self.assertIn("blocked by egress filter", got["error"])

    def test_payload_is_unique_per_check(self):
        receiver = self._receiver()
        first = check("http://10.0.0.5/a?id=1", "id", transport=transport())
        second = check("http://10.0.0.5/b?id=1", "id", transport=transport())
        self.assertNotEqual(first["token"], second["token"])
        self.assertIn(first["token"], first["payload"])
        self.assertIn(second["token"], second["payload"])
        self.assertTrue(first["payload"].startswith(receiver.base_url))

    def test_target_field_shows_probe_address(self):
        self._receiver()
        got = check("http://10.0.0.5/item?a=1", "id", transport=transport())
        self.assertEqual(got["target"].split("?")[0], "http://10.0.0.5/item")
        self.assertIn("a=1", got["target"])
        self.assertIn("id=", got["target"])

    def test_missing_param_is_appended(self):
        self._receiver()
        got = check("http://10.0.0.5/item?a=1", "next", transport=transport())
        self.assertIn("next=", got["target"])


class DedupeTest(unittest.TestCase):
    def setUp(self):
        shutdown()
        configure(receiver=StubReceiver(), timeout=0.2, transport=None)
        reset()

    def tearDown(self):
        shutdown()

    def test_same_pair_reuses_conclusion(self):
        url = "http://10.0.0.5/item?id=1"
        first = check(url, "id", transport=transport(body="root:x:0:0"))
        second = check(url, "id", transport=transport(body="root:x:0:0"))
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["attempts"], 2)

        data = report()
        self.assertEqual(data["checks"], 2)
        self.assertEqual(data["pairs"], 1)
        self.assertEqual(data["duplicates"], 1)
        self.assertEqual(len(data["suspicious"]), 1)

    def test_distinct_params_are_kept_apart(self):
        url = "http://10.0.0.5/item?id=1"
        check(url, "id", transport=transport(body="root:x:0:0"))
        check(url, "next", transport=transport(body="root:x:0:0"))
        data = report()
        self.assertEqual(data["pairs"], 2)
        self.assertEqual(data["duplicates"], 0)
        self.assertEqual(len(data["suspicious"]), 2)

    def test_force_retries(self):
        url = "http://10.0.0.5/item?id=1"
        first = check(url, "id", transport=transport())
        second = check(url, "id", transport=transport(), force=True)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["attempts"], 1)
        self.assertFalse(second["reused"])
        self.assertEqual(report()["pairs"], 1)


class ReportTest(unittest.TestCase):
    def setUp(self):
        shutdown()
        self.receiver = StubReceiver()
        configure(receiver=self.receiver, timeout=0.2, transport=None)
        reset()

    def tearDown(self):
        shutdown()

    def test_report_matches_receiver_records(self):
        def relay(payload):
            self.receiver.add(payload.rsplit("/", 1)[-1])

        hit = check(
            "http://10.0.0.5/item?id=1", "id", transport=transport(on_payload=relay)
        )
        check(
            "http://10.0.0.5/item?id=2", "id", transport=transport(body="169.254.169.254")
        )

        data = report()
        self.assertEqual(data["round_status"], "ok")
        self.assertEqual(len(data["hits"]), 1)
        self.assertEqual(data["hits"][0]["id"], hit["id"])
        self.assertEqual([cb["id"] for cb in data["callbacks"]], [hit["token"]])
        self.assertEqual(len(data["suspicious"]), 1)
        self.assertEqual(
            data["suspicious"][0]["reason"], "internal_marker:169.254.169.254"
        )
        self.assertEqual(data["receiver"]["records"], 1)

    def test_report_ignores_foreign_callbacks(self):
        self.receiver.add("not-ours")
        check("http://10.0.0.5/item?id=1", "id", transport=transport())
        data = report()
        self.assertEqual(data["callbacks"], [])
        self.assertEqual(data["hits"], [])

    def test_reset_clears_round_but_keeps_receiver_audit(self):
        def relay(payload):
            self.receiver.add(payload.rsplit("/", 1)[-1])

        check("http://10.0.0.5/item?id=1", "id", transport=transport(on_payload=relay))
        self.assertEqual(len(callbacks()), 1)
        reset()
        self.assertEqual(report()["checks"], 0)
        self.assertEqual(len(callbacks()), 1)


class ReceiverDownTest(unittest.TestCase):
    def setUp(self):
        shutdown()
        reset()

    def tearDown(self):
        configure(receiver=None, timeout=0.3, transport=None)
        shutdown()

    def test_unavailable_receiver_raises_and_halts(self):
        configure(receiver=StubReceiver(up=False))
        with self.assertRaises(ReceiverUnavailable):
            check("http://10.0.0.5/item?id=1", "id", transport=transport())
        # 本轮已停止：后续调用直接报错，不会给出「没有回连=安全」的结论
        with self.assertRaises(ReceiverUnavailable):
            check("http://10.0.0.5/item?id=2", "id", transport=transport())
        data = report()
        self.assertEqual(data["round_status"], "aborted")
        self.assertTrue(data["error"])
        self.assertEqual(data["hits"], [])

    def test_receiver_without_base_url_is_unavailable(self):
        class NoUrl(StubReceiver):
            base_url = ""

        configure(receiver=NoUrl())
        with self.assertRaises(ReceiverUnavailable):
            check("http://10.0.0.5/item?id=1", "id", transport=transport())

    def test_check_requires_args(self):
        configure(receiver=StubReceiver())
        with self.assertRaises(ValueError):
            check("http://10.0.0.5/item", "  ")
        with self.assertRaises(ValueError):
            check("", "id")


class EmbeddedReceiverTest(unittest.TestCase):
    def setUp(self):
        shutdown()
        reset()

    def tearDown(self):
        shutdown()

    def test_end_to_end_callback_with_headers(self):
        receiver = EmbeddedReceiver().start()
        self.addCleanup(receiver.stop)
        self.assertTrue(receiver.available())
        configure(receiver=receiver, timeout=2.0)

        def target_fetches(payload):
            request = urllib.request.Request(
                payload, headers={"X-Forwarded-For": "203.0.113.7"}
            )
            urllib.request.urlopen(request, timeout=2).read()

        got = check(
            "http://10.0.0.5/item?id=1",
            "id",
            transport=transport(on_payload=target_fetches),
        )
        self.assertEqual(got["status"], "hit")
        self.assertEqual(got["via"], "proxy")
        self.assertTrue(got["forwarded"])
        self.assertEqual(got["source_address"], "127.0.0.1")
        self.assertEqual(got["callback"]["client_address"], "203.0.113.7")
        self.assertIn("x-forwarded-for", got["callback"]["proxy_headers"])

    def test_direct_callback_is_not_forwarded(self):
        receiver = EmbeddedReceiver().start()
        self.addCleanup(receiver.stop)
        configure(receiver=receiver, timeout=2.0)

        def target_fetches(payload):
            urllib.request.urlopen(payload, timeout=2).read()

        got = check(
            "http://10.0.0.5/item?id=1", "id", transport=transport(on_payload=target_fetches)
        )
        self.assertEqual(got["status"], "hit")
        self.assertEqual(got["via"], "direct")
        self.assertFalse(got["forwarded"])
        self.assertEqual(got["source_address"], "127.0.0.1")

    def test_default_receiver_starts_lazily(self):
        configure(receiver=None, timeout=0.3)
        got = check("http://10.0.0.5/item?id=1", "id", transport=transport())
        self.assertEqual(got["status"], "clean")
        self.assertTrue(report()["receiver"]["available"])
        self.assertIn("http://127.0.0.1:", got["payload"])


if __name__ == "__main__":
    unittest.main()
