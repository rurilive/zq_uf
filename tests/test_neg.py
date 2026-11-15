import gzip
import unittest

from compressneg import Negotiator, decode, headers


def gz(text, enc="utf-8"):
    return gzip.compress(text.encode(enc))


def fake_fetch(support, down=False):
    """构造一个只支持指定压缩方式的站点；down=True 时连 identity 都失败。"""
    def fetch(site, accept_encoding):
        if down:
            raise RuntimeError("连接失败")
        if accept_encoding == "identity":
            return {"body": "正文".encode(), "headers": {}}
        if accept_encoding not in support:
            raise RuntimeError("406 不接受的压缩")
        if accept_encoding == "gzip":
            return {"body": gz("正文"),
                    "headers": {"content-encoding": "gzip",
                                "content-type": "text/html; charset=utf-8"}}
        raise RuntimeError("unexpected")
    return fetch


class NegotiateTest(unittest.TestCase):
    def test_unknown_site_stays_identity(self):
        self.assertEqual(headers("new"), {"accept-encoding": "identity"})

    def test_negotiate_gzip_site(self):
        neg = Negotiator(fetch=fake_fetch({"gzip"}))
        rec = neg.negotiate("shop")
        self.assertEqual(rec.accept_encoding, "gzip")
        self.assertEqual(rec.charset, "utf-8")
        self.assertEqual(neg.headers("shop"), {"accept-encoding": "gzip"})

    def test_fallback_identity_when_compression_rejected(self):
        neg = Negotiator(fetch=fake_fetch(set()))
        rec = neg.negotiate("plain")
        self.assertEqual(rec.accept_encoding, "identity")
        self.assertIsNone(rec.last_error)  # 退回不压缩是正常结果

    def test_total_failure_recorded_on_site(self):
        neg = Negotiator(fetch=fake_fetch(set(), down=True))
        rec = neg.negotiate("dead")
        self.assertEqual(rec.accept_encoding, "identity")
        self.assertIsNotNone(rec.last_error)

    def test_selfcheck_failure_scoped_to_site(self):
        neg = Negotiator(fetch=fake_fetch({"gzip"}, down=False))
        neg.fetch = lambda site, enc: fake_fetch({"gzip"}, down=(site == "dead"))(site, enc)
        bad = neg.selfcheck("dead")
        self.assertFalse(bad["ok"])
        good = neg.selfcheck("shop")
        self.assertTrue(good["ok"])

    def test_manual_override_locks(self):
        neg = Negotiator(fetch=fake_fetch({"gzip"}))
        neg.negotiate("shop")
        neg.override("shop", accept_encoding="identity", charset="gbk")
        rec = neg.negotiate("shop")  # 锁定后自动探测不改回
        self.assertEqual(rec.accept_encoding, "identity")
        self.assertEqual(rec.charset, "gbk")
        self.assertTrue(rec.locked)
        neg.unlock("shop")
        self.assertEqual(neg.negotiate("shop").accept_encoding, "gzip")


class DecodeTest(unittest.TestCase):
    def test_gzip_utf8(self):
        neg = Negotiator()
        result = neg.decode({"body": gz("中文"),
                             "headers": {"content-encoding": "gzip",
                                         "content-type": "text/html; charset=utf-8"}},
                            site="s")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["decompressed"])
        self.assertEqual(result["text"], "中文")
        self.assertEqual(result["encoding"], "utf-8")

    def test_sniff_charset_when_undeclared(self):
        neg = Negotiator()
        result = neg.decode({"body": "中文".encode("gbk"), "headers": {}})
        self.assertEqual(result["encoding"], "gb18030")
        self.assertEqual(result["text"], "中文")

    def test_decompress_failure_keeps_raw(self):
        neg = Negotiator()
        bad = b"not-really-gzip"
        result = neg.decode({"body": bad,
                             "headers": {"content-encoding": "gzip"}}, site="s")
        self.assertTrue(result["failed"])
        self.assertEqual(result["status"], "decompress_failed")
        self.assertEqual(result["raw"], bad)
        self.assertIn("gzip", result["failure_reason"])
        self.assertEqual(neg.retained["s"], [bad])  # 保留到排查结束
        neg.clear_retained("s")
        self.assertNotIn("s", neg.retained)

    def test_unsupported_zstd_marked_not_dropped(self):
        neg = Negotiator()
        result = neg.decode({"body": b"\x28\xb5\x2f\xfd",
                             "headers": {"content-encoding": "zstd"}})
        self.assertTrue(result["failed"])
        self.assertEqual(result["status"], "decompress_failed")

    def test_empty_body_explicit_status(self):
        neg = Negotiator()
        result = neg.decode({"body": b""})
        self.assertEqual(result["status"], "empty")
        self.assertFalse(result["failed"])

    def test_module_level_decode_still_works(self):
        self.assertEqual(decode({"body": "中文".encode()})["text"], "中文")


if __name__ == "__main__":
    unittest.main()
