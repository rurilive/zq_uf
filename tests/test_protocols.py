import unittest

from pcapflow import analyze, load, reset
from tests.test_flow import pkt


class ProtocolParseTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_port_based_identification(self):
        load("p", [
            pkt(1, "10.0.0.5", "10.0.0.9", 445),
            pkt(2, "10.0.0.5", "203.0.113.7", 443),
        ])
        out = analyze("p")
        apps = {s["dport"]: s["app"] for s in out["session_list"]}
        self.assertEqual(apps[445], "smb")
        self.assertEqual(apps[443], "tls")
        self.assertEqual(out["failed"], [])

    def test_payload_sniff_without_port_hint(self):
        load("http_raw", [
            pkt(1, "10.0.0.5", "203.0.113.7", 39999,
                payload="GET /index.html HTTP/1.1\r\nHost: example.com\r\n"),
        ])
        out = analyze("http_raw")
        sess = out["session_list"][0]
        self.assertEqual(sess["app"], "http")

    def test_malformed_tls_listed_as_failed_packet(self):
        load("bad_tls", [
            pkt(1, "10.0.0.5", "203.0.113.7", 443, payload=b"\x01\x02\x03garbage"),
        ])
        out = analyze("bad_tls")
        self.assertEqual(len(out["failed"]), 1)
        self.assertEqual(out["failed"][0]["packet"], 1)
        self.assertEqual(out["failed"][0]["app"], "tls")

    def test_malformed_dns_listed(self):
        load("bad_dns", [
            pkt(1, "10.0.0.5", "10.0.0.53", 53, proto="udp",
                payload=b"\x00" * 8),
        ])
        out = analyze("bad_dns")
        self.assertEqual(out["failed"][0]["app"], "dns")

    def test_uncovered_protocol_listed_separately(self):
        load("unknown", [
            pkt(1, "10.0.0.5", "203.0.113.7", 31337),
            pkt(2, "10.0.0.5", "203.0.113.7", 31337),
        ])
        out = analyze("unknown")
        self.assertEqual(len(out["uncovered"]), 1)
        self.assertEqual(out["uncovered"][0]["packets"], [1, 2])

    def test_empty_payload_does_not_fail(self):
        load("no_payload", [pkt(1, "10.0.0.5", "203.0.113.7", 22)])
        out = analyze("no_payload")
        self.assertEqual(out["failed"], [])
        self.assertEqual(out["session_list"][0]["app"], "ssh")

    def test_custom_protocol_registration(self):
        from pcapflow.protocols import Protocols
        from pcapflow.session import build_sessions

        def parse_foo(packet, payload):
            if payload is not None and not payload.startswith(b"FOO/1"):
                return "不是 FOO 协议", {}
            return None, {}

        reg = Protocols()
        reg.register("foo", (4242,), parse_foo)
        sessions, failed = build_sessions(
            [pkt(1, "10.0.0.1", "10.0.0.2", 4242)], protocols=reg
        )
        self.assertEqual(sessions[0].app, "foo")
        self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
