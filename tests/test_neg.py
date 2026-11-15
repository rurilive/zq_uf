import gzip
import unittest

from compressneg import Negotiator, detect_charset, offer_list


def gz(text, encoding="utf-8"):
    return gzip.compress(text.encode(encoding))


class ProbeTest(unittest.TestCase):
    def test_unknown_site_gets_full_offer(self):
        neg = Negotiator()
        offered = neg.headers("new-site")["accept-encoding"]
        self.assertEqual(offered, ", ".join(offer_list()))
        self.assertIn("gzip", offered)

    def test_negotiated_encoding_remembered_per_site(self):
        neg = Negotiator()
        neg.decode("a", {"body": gz("正文"),
                         "headers": {"content-encoding": "gzip"}})
        self.assertEqual(neg.headers("a"), {"accept-encoding": "gzip"})
        # 其他站点不受影响
        self.assertIn("gzip", neg.headers("b")["accept-encoding"])

    def test_identity_site_recorded(self):
        neg = Negotiator()
        neg.decode("plain", {"body": "ok".encode()})
        self.assertEqual(neg.headers("plain"), {"accept-encoding": "identity"})


class DecodeTest(unittest.TestCase):
    def setUp(self):
        self.neg = Negotiator()

    def test_gzip_body_decoded(self):
        r = self.neg.decode("a", {"body": gz("中文正文"),
                                  "headers": {"content-encoding": "gzip"}})
        self.assertTrue(r["ok"])
        self.assertTrue(r["decompressed"])
        self.assertEqual(r["text"], "中文正文")
        self.assertEqual(r["content_encoding"], "gzip")

    def test_charset_from_header(self):
        body = "中文".encode("gbk")
        r = self.neg.decode("a", {"body": body,
                                  "headers": {"content-type": "text/html; charset=gbk"}})
        self.assertEqual(r["text"], "中文")
        self.assertEqual(r["encoding"], "gbk")
        self.assertEqual(r["charset_source"], "header")

    def test_charset_sniffed_when_header_missing(self):
        # 无编码头时从内容判断，中文不能变问号
        r = self.neg.decode("a", {"body": "中文".encode("utf-8")})
        self.assertEqual(r["text"], "中文")
        self.assertEqual(r["charset_source"], "content")

    def test_charset_from_meta(self):
        html = '<meta charset="gbk"><p>中文</p>'.encode("gbk")
        self.assertEqual(detect_charset(html)[0], "gbk")

    def test_empty_body_has_explicit_status(self):
        r = self.neg.decode("a", {"body": b""})
        self.assertEqual(r["status"], "empty")
        self.assertFalse(r["ok"])
        self.assertEqual(r["failure"]["stage"], "empty")


class FailureTest(unittest.TestCase):
    def setUp(self):
        self.neg = Negotiator()

    def test_decompress_failure_keeps_raw_and_marks(self):
        bad = b"\x1f\x8b broken gzip"
        r = self.neg.decode("a", {"body": bad,
                                  "headers": {"content-encoding": "gzip"}})
        self.assertEqual(r["status"], "decompress_failed")
        self.assertFalse(r["ok"])
        self.assertEqual(r["raw"], bad)  # 原始字节保留，不当空页面
        self.assertIn("gzip", r["failure"]["reason"])

    def test_failure_falls_back_to_identity(self):
        self.neg.decode("a", {"body": b"bad",
                              "headers": {"content-encoding": "gzip"}})
        self.assertEqual(self.neg.headers("a"), {"accept-encoding": "identity"})

    def test_failed_raw_retained_until_cleared(self):
        resp = {"body": b"bad", "headers": {"content-encoding": "gzip"}}
        self.neg.decode("a", resp)
        self.assertEqual(self.neg.failed_responses("a"), [resp])
        self.neg.clear_failed("a")  # 排查结束后清除
        self.assertEqual(self.neg.failed_responses("a"), [])

    def test_unsupported_codec_is_per_site_failure(self):
        # 本机无 br/zstd 解码器时：标失败、留原始字节、回退 identity
        r = self.neg.decode("a", {"body": b"\x00\x01",
                                  "headers": {"content-encoding": "br"}})
        self.assertEqual(r["status"], "decompress_failed")
        self.assertEqual(r["raw"], b"\x00\x01")
        self.assertEqual(self.neg.headers("a"), {"accept-encoding": "identity"})


class OverrideTest(unittest.TestCase):
    def setUp(self):
        self.neg = Negotiator()

    def test_override_locks_record(self):
        self.neg.override("a", accept_encoding="gzip", charset="utf-8")
        rec = self.neg.record("a")
        self.assertTrue(rec["locked"])
        # 锁定后实际响应不同也不再自动改回
        self.neg.decode("a", {"body": "x".encode(),
                              "headers": {"content-encoding": "identity"}})
        self.assertEqual(self.neg.record("a")["accept_encoding"], "gzip")
        self.assertEqual(self.neg.headers("a"), {"accept-encoding": "gzip"})

    def test_override_charset_wins(self):
        self.neg.override("a", charset="gbk")
        r = self.neg.decode("a", {"body": "中文".encode("gbk")})
        self.assertEqual(r["text"], "中文")
        self.assertEqual(r["charset_source"], "override")

    def test_unlock_triggers_reprobe(self):
        self.neg.override("a", accept_encoding="gzip")
        self.neg.unlock("a")  # 站点改版，解锁重探
        self.assertFalse(self.neg.record("a")["locked"])
        self.assertIn("gzip", self.neg.headers("a")["accept-encoding"])


class SelfcheckTest(unittest.TestCase):
    def test_selfcheck_ok(self):
        neg = Negotiator()
        report = neg.selfcheck("a", {"body": gz("ok"),
                                     "headers": {"content-encoding": "gzip"}})
        self.assertTrue(report["ok"])

    def test_selfcheck_failure_only_affects_that_site(self):
        neg = Negotiator()
        report = neg.selfcheck("bad", {"body": b"bad",
                                       "headers": {"content-encoding": "gzip"}})
        self.assertFalse(report["ok"])
        self.assertEqual(neg.headers("bad"), {"accept-encoding": "identity"})
        # 其他站点仍正常探测
        self.assertIn("gzip", neg.headers("good")["accept-encoding"])


if __name__ == "__main__":
    unittest.main()
