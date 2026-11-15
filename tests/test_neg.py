import gzip
import unittest
import zlib

from compressneg import (
    available_encodings,
    decode,
    headers,
    override,
    profile,
    profiles,
    reset,
)


class NegTest(unittest.TestCase):
    def test_headers_are_fixed(self):
        self.assertEqual(headers("a"), {"accept-encoding": "gzip"})

    def test_decode_utf8(self):
        self.assertEqual(decode({"body": "中文".encode()})["text"], "中文")

    def test_failure_flag_false(self):
        self.assertFalse(decode({"body": b"x"})["failed"])


class NegotiationTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_probe_order_then_lock(self):
        # 首轮探测 gzip；gzip 正常响应后锁定
        self.assertEqual(headers("shop")["accept-encoding"], "gzip")
        body = gzip.compress("正文".encode())
        out = decode({"site": "shop",
                      "headers": {"Content-Encoding": "gzip"},
                      "body": body})
        self.assertFalse(out["failed"])
        self.assertEqual(out["encoding"], "gzip")
        self.assertEqual(out["text"], "正文")
        self.assertEqual(profile("shop")["state"], "locked")
        self.assertEqual(headers("shop")["accept-encoding"], "gzip, identity")

    def test_fallback_to_identity(self):
        # 站点不认 gzip，返回未压缩内容（无 content-encoding）
        headers("legacy")
        out = decode({"site": "legacy", "headers": {},
                      "body": "纯文本".encode()})
        self.assertFalse(out["failed"])
        self.assertEqual(out["encoding"], "identity")
        # gzip 记入失败档，下一轮自动回退 identity 并兜底
        self.assertIn("gzip", profile("legacy")["failed_encodings"])
        self.assertEqual(headers("legacy")["accept-encoding"], "identity")
        # identity 成功后锁定
        again = decode({"site": "legacy", "headers": {},
                        "body": b"ok"})
        self.assertEqual(profile("legacy")["state"], "locked")
        self.assertEqual(again["encoding"], "identity")

    def test_corrupt_gzip_marks_failure_and_keeps_raw(self):
        headers("broken")
        raw = b"\x1f\x8b\x08\x00not-a-real-gzip-stream"
        out = decode({"site": "broken",
                      "headers": {"Content-Encoding": "gzip"},
                      "body": raw})
        self.assertTrue(out["failed"])
        self.assertIn("解压失败", out["reason"])
        self.assertIn("gzip", out["reason"])
        # 原始字节原样保留，供排查与重试
        self.assertEqual(out["raw"], raw)
        self.assertIsNone(out["decompressed"])
        self.assertIn("gzip", profile("broken")["failed_encodings"])
        # 下一轮请求自动换档，压缩失败不阻塞重试
        self.assertNotEqual(headers("broken")["accept-encoding"], "gzip")

    def test_failure_then_retry_succeeds(self):
        headers("flaky")
        decode({"site": "flaky",
                "headers": {"Content-Encoding": "gzip"},
                "body": b"broken-gzip"})
        # 服务端下一轮按 identity 返回，协商恢复成功并锁定
        out = decode({"site": "flaky", "headers": {},
                      "body": "恢复".encode("utf-8")})
        self.assertFalse(out["failed"])
        self.assertEqual(out["text"], "恢复")
        self.assertEqual(profile("flaky")["state"], "locked")
        self.assertEqual(profile("flaky")["encoding"], "identity")

    def test_manual_override(self):
        override("forced", encoding="identity", charset="utf-8")
        self.assertEqual(headers("forced"),
                         {"accept-encoding": "identity",
                          "accept-charset": "utf-8"})
        out = decode({"site": "forced", "headers": {}, "body": b"x"})
        self.assertEqual(profile("forced")["source"], "manual")
        self.assertEqual(out["charset"], "utf-8")
        self.assertEqual(out["charset_source"], "cached")

    def test_override_rejects_unsupported(self):
        with self.assertRaises(ValueError):
            override("x", encoding="unknown-coding")
        if "br" not in available_encodings():
            with self.assertRaises(ValueError):
                override("x", encoding="br")

    def test_reset_clears_cache(self):
        decode({"site": "tmp", "headers": {}, "body": b"x"})
        self.assertIsNotNone(profile("tmp"))
        reset("tmp")
        self.assertIsNone(profile("tmp"))
        decode({"site": "tmp2", "headers": {}, "body": b"x"})
        reset()
        self.assertEqual(profiles(), {})


class CharsetDetectionTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_charset_from_content_type(self):
        body = "中文".encode("gb18030")
        out = decode({"site": "gb",
                      "headers": {"Content-Type": "text/html; charset=GB18030"},
                      "body": body})
        self.assertEqual(out["charset"], "gb18030")
        self.assertEqual(out["charset_source"], "content-type")
        self.assertEqual(out["text"], "中文")

    def test_charset_sniff_utf8_without_header(self):
        out = decode({"site": "nohead", "headers": {},
                      "body": "中文".encode("utf-8")})
        self.assertEqual(out["charset"], "utf-8")
        self.assertEqual(out["charset_source"], "sniff")

    def test_charset_meta_declaration(self):
        html = ('<html><head><meta charset="gbk"></head>'
                '<body>中文</body>').encode("gbk")
        out = decode({"site": "meta", "headers": {}, "body": html})
        self.assertEqual(out["charset"], "gbk")
        self.assertEqual(out["text"], html.decode("gbk"))
        self.assertIn("中文", out["text"])

    def test_bom_detection(self):
        out = decode({"site": "bom", "headers": {},
                      "body": "﻿中文".encode("utf-8-sig")})
        self.assertEqual(out["charset"], "utf-8-sig")
        self.assertEqual(out["charset_source"], "bom")

    def test_gzip_plus_charset_header(self):
        body = gzip.compress("中文".encode("gb18030"))
        out = decode({"site": "gz-gb",
                      "headers": {"Content-Encoding": "gzip",
                                  "Content-Type": "text/html; charset=gb18030"},
                      "body": body})
        self.assertFalse(out["failed"])
        self.assertEqual(out["encoding"], "gzip")
        self.assertEqual(out["charset"], "gb18030")
        self.assertEqual(out["text"], "中文")

    def test_deflate_support(self):
        body = zlib.compress(b"deflate-body")
        out = decode({"site": "dz",
                      "headers": {"Content-Encoding": "deflate"},
                      "body": body})
        self.assertFalse(out["failed"], out["reason"])
        self.assertEqual(out["text"], "deflate-body")

    def test_gzip_failure_falls_through_deflate_to_identity(self):
        site = "cascade"
        # 第 1 轮：gzip 探测，响应损坏
        self.assertEqual(headers(site)["accept-encoding"], "gzip")
        decode({"site": site, "headers": {"Content-Encoding": "gzip"},
                "body": b"broken"})
        # 第 2 轮：换档 deflate，服务端仍只给 identity
        self.assertEqual(headers(site)["accept-encoding"],
                         "deflate, identity")
        out = decode({"site": site, "headers": {}, "body": b"plain"})
        self.assertFalse(out["failed"])
        self.assertEqual(profile(site)["encoding"], "identity")
        # 两档压缩都未被站点接受，逐级记账后锁定 identity
        self.assertEqual(profile(site)["failed_encodings"], ["gzip", "deflate"])
        self.assertEqual(headers(site)["accept-encoding"], "identity")

    def test_chained_content_encoding(self):
        payload = b"nested-body"
        body = gzip.compress(zlib.compress(payload))
        out = decode({"site": "chain",
                      "headers": {"Content-Encoding": "deflate, gzip"},
                      "body": body})
        self.assertFalse(out["failed"], out["reason"])
        self.assertEqual(out["encoding"], "deflate, gzip")
        self.assertEqual(out["decompressed"], payload)

    def test_unsupported_coding_marked_with_raw(self):
        raw = b"some-brotli-bytes"
        out = decode({"site": "bronly",
                      "headers": {"Content-Encoding": "br"},
                      "body": raw})
        if "br" in available_encodings():
            self.skipTest("环境已装 brotli，跳过不支持场景")
        self.assertTrue(out["failed"])
        self.assertEqual(out["raw"], raw)
        self.assertIn("br", out["reason"])

    def test_decode_without_site_keeps_stateless(self):
        out = decode({"body": "无站点".encode("utf-8")})
        self.assertFalse(out["failed"])
        self.assertEqual(out["text"], "无站点")
        self.assertIsNone(out["site"])
        self.assertEqual(profiles(), {})


if __name__ == "__main__":
    unittest.main()
