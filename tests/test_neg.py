import gzip
import os
import tempfile
import unittest
import zlib

from compressneg import (Negotiator, clear_failed, decode, detect_charset,
                         failed_responses, headers, offer_list, override,
                         record, records, selfcheck, unlock)
from compressneg import neg


def gz(text, encoding="utf-8"):
    return gzip.compress(text.encode(encoding))


def zstd_bytes(text):
    codec = neg._CODECS["zstd"]
    if not codec.available:
        raise unittest.SkipTest("本机没有 zstd 解码器")
    return codec.encode(text.encode("utf-8"))


def brotli_bytes(text):
    return brotli_raw(text.encode("utf-8"))


def brotli_raw(raw):
    codec = neg._CODECS["br"]
    if not codec.available or codec.encode is None:
        raise unittest.SkipTest("本机没有可编码的 brotli")
    return codec.encode(bytes(raw))


class LegacyTest(unittest.TestCase):
    """原有断言：接口不变，语义随协商结果变化。"""

    def test_decode_utf8(self):
        self.assertEqual(decode({"body": "中文".encode()})["text"], "中文")

    def test_failure_flag_false(self):
        self.assertFalse(decode({"body": b"x"})["failed"])

    def test_module_headers_do_not_crash(self):
        # 原来断言固定 {gzip}；现在首个请求是全量探测，仍是一个 dict。
        result = headers("legacy-module-site")
        self.assertIn("accept-encoding", result)


class ProbeTest(unittest.TestCase):
    def test_unknown_site_offers_everything_local_codecs_support(self):
        neg_ = Negotiator()
        offered = neg_.headers("new-site")["accept-encoding"]
        self.assertEqual(offered, ", ".join(offer_list()))
        self.assertIn("gzip", offered)

    def test_only_locally_decodable_codecs_are_offered(self):
        # 本地解不开的方式不能声明，否则站点按它压缩我们就解不出来。
        self.assertEqual(
            set(offer_list()),
            {name for name, codec in neg._CODECS.items()
             if codec.available and name != "identity"})

    def test_negotiated_encoding_remembered_per_site(self):
        neg_ = Negotiator()
        neg_.decode("a", {"body": gz("正文"),
                          "headers": {"content-encoding": "gzip"}})
        self.assertEqual(neg_.headers("a"), {"accept-encoding": "gzip"})
        # 其他站点不受影响
        self.assertIn("gzip", neg_.headers("b")["accept-encoding"])

    def test_identity_site_recorded(self):
        neg_ = Negotiator()
        neg_.decode("plain", {"body": "ok".encode()})
        self.assertEqual(neg_.headers("plain"), {"accept-encoding": "identity"})

    def test_zstd_site_remembered(self):
        neg_ = Negotiator()
        neg_.decode("z", {"body": zstd_bytes("正文"),
                          "headers": {"content-encoding": "zstd"}})
        self.assertEqual(neg_.headers("z"), {"accept-encoding": "zstd"})


class DecodeTest(unittest.TestCase):
    def setUp(self):
        self.neg = Negotiator()

    def test_gzip_body_decoded(self):
        r = self.neg.decode("a", {"body": gz("中文正文"),
                                  "headers": {"content-encoding": "gzip"}})
        self.assertTrue(r["ok"])
        self.assertTrue(r["decompressed"])
        self.assertEqual(r["decompression"], "ok")
        self.assertEqual(r["text"], "中文正文")
        self.assertEqual(r["content_encoding"], "gzip")

    def test_brotli_body_decoded(self):
        r = self.neg.decode("a", {"body": brotli_bytes("br 正文"),
                                  "headers": {"content-encoding": "br"}})
        self.assertTrue(r["ok"], r["reason"])
        self.assertEqual(r["text"], "br 正文")
        self.assertEqual(r["layers"], ["br"])

    def test_zstd_body_decoded(self):
        r = self.neg.decode("a", {"body": zstd_bytes("zstd 正文"),
                                  "headers": {"content-encoding": "zstd"}})
        self.assertTrue(r["ok"], r["reason"])
        self.assertEqual(r["text"], "zstd 正文")
        self.assertEqual(r["layers"], ["zstd"])

    def test_multiple_layers_applied_in_reverse(self):
        inner = gz("多层正文")
        body = brotli_raw(inner)
        r = self.neg.decode("a", {"body": body,
                                  "headers": {"content-encoding": "gzip, br"}})
        self.assertTrue(r["ok"], r["reason"])
        self.assertEqual(r["text"], "多层正文")
        self.assertEqual(r["layers"], ["br", "gzip"])

    def test_plain_deflate_and_zlib_deflate(self):
        wrapped = zlib.compress("deflate 正文".encode())
        r = self.neg.decode("a", {"body": wrapped,
                                  "headers": {"content-encoding": "deflate"}})
        self.assertEqual(r["text"], "deflate 正文")
        compressor = zlib.compressobj(wbits=-15)
        raw = compressor.compress("裸 deflate".encode()) + compressor.flush()
        r = self.neg.decode("b", {"body": raw,
                                  "headers": {"content-encoding": "deflate"}})
        self.assertEqual(r["text"], "裸 deflate")

    def test_charset_from_header(self):
        body = "中文".encode("gbk")
        r = self.neg.decode("a", {"body": body,
                                  "headers": {"content-type":
                                              "text/html; charset=gbk"}})
        self.assertEqual(r["text"], "中文")
        self.assertEqual(r["charset_source"], "header")

    def test_charset_sniffed_when_header_missing(self):
        # 无编码头时从内容判断，中文不能变问号
        r = self.neg.decode("a", {"body": "中文".encode("utf-8")})
        self.assertEqual(r["text"], "中文")
        self.assertEqual(r["charset_source"], "content")

    def test_charset_from_bom(self):
        r = self.neg.decode("a", {"body": b"\xef\xbb\xbf" + "中文".encode()})
        self.assertEqual(r["encoding"], "utf-8")
        self.assertEqual(r["charset_source"], "bom")
        self.assertEqual(r["text"], "中文")

    def test_charset_from_meta(self):
        html = '<meta charset="gbk"><p>中文</p>'.encode("gbk")
        self.assertEqual(detect_charset(html)[0], "gbk")
        r = self.neg.decode("a", {"body": html})
        self.assertEqual(r["encoding"], "gbk")
        self.assertEqual(r["charset_source"], "meta")

    def test_gbk_without_any_hint_does_not_become_question_marks(self):
        r = self.neg.decode("a", {"body": "中文没有编码头".encode("gbk")})
        self.assertTrue(r["ok"])
        self.assertEqual(r["text"], "中文没有编码头")
        self.assertNotIn("?", r["text"])

    def test_empty_body_has_explicit_status(self):
        r = self.neg.decode("a", {"body": b""})
        self.assertEqual(r["status"], "empty")
        self.assertFalse(r["ok"])
        self.assertEqual(r["failure"]["stage"], "empty")

    def test_decode_without_site(self):
        r = decode({"body": "无站点".encode()})
        self.assertEqual(r["text"], "无站点")
        self.assertIsNone(r["site"])


class ResultShapeTest(unittest.TestCase):
    """返回里必须给出协商结果、实际编码、是否解压成功和失败原因。"""

    def setUp(self):
        self.neg = Negotiator()

    def test_ok_result_reports_negotiation_encoding_and_decompression(self):
        r = self.neg.decode("a", {"body": gz("正文"),
                                  "headers": {"content-encoding": "gzip"}})
        for key in ("negotiation", "encoding", "content_encoding",
                    "decompressed", "decompression", "failure", "ok"):
            self.assertIn(key, r)
        self.assertEqual(r["encoding"], "utf-8")
        self.assertEqual(r["negotiation"]["accept_encoding"], "gzip")
        self.assertEqual(r["negotiation"]["source"], "probe")
        self.assertIsNone(r["failure"])

    def test_second_decode_reportn_remembered_source(self):
        self.neg.decode("a", {"body": gz("一"),
                              "headers": {"content-encoding": "gzip"}})
        r = self.neg.decode("a", {"body": gz("二"),
                                  "headers": {"content-encoding": "gzip"}})
        self.assertEqual(r["negotiation"]["source"], "remembered")

    def test_failure_result_carries_reason_and_stage(self):
        r = self.neg.decode("a", {"body": b"bad",
                                  "headers": {"content-encoding": "gzip"}})
        self.assertEqual(r["failure"]["stage"], "decompress")
        self.assertIn("gzip", r["failure"]["reason"])
        self.assertEqual(r["reason"], r["failure"]["reason"])


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
        self.assertIsNone(r["text"])
        self.assertIn("gzip", r["failure"]["reason"])

    def test_failure_falls_back_to_identity(self):
        self.neg.decode("a", {"body": b"bad",
                              "headers": {"content-encoding": "gzip"}})
        self.assertEqual(self.neg.headers("a"), {"accept-encoding": "identity"})

    def test_failure_is_recorded_for_inspection(self):
        self.neg.decode("a", {"body": b"bad",
                              "headers": {"content-encoding": "gzip"}})
        rec = self.neg.record("a")
        self.assertTrue(rec["fallback"])
        self.assertEqual(rec["failures"][-1]["stage"], "decompress")
        self.assertEqual(rec["last"]["status"], "decompress_failed")

    def test_failed_raw_retained_until_cleared(self):
        resp = {"body": b"bad", "headers": {"content-encoding": "gzip"}}
        self.neg.decode("a", resp)
        self.assertEqual(self.neg.failed_responses("a")[0]["body"], b"bad")
        self.neg.clear_failed("a")  # 排查结束后清除
        self.assertEqual(self.neg.failed_responses("a"), [])

    def test_unknown_codec_is_per_site_failure(self):
        r = self.neg.decode("a", {"body": b"\x00\x01",
                                  "headers": {"content-encoding": "compress"}})
        self.assertEqual(r["status"], "decompress_failed")
        self.assertEqual(r["raw"], b"\x00\x01")
        self.assertEqual(self.neg.headers("a"), {"accept-encoding": "identity"})

    def test_decode_failure_does_not_return_question_marks(self):
        # 声明 gbk 但字节解不开：不能把替换成问号的文本当正文。
        r = self.neg.decode("a", {"body": b"\xff\xfe\xfa",
                                  "headers": {"content-type":
                                              "text/html; charset=utf-8"}})
        self.assertEqual(r["status"], "decode_failed")
        self.assertIsNone(r["text"])
        self.assertFalse(r["ok"])

    def test_rejected_site_falls_back_to_identity(self):
        r = self.neg.decode("a", {"body": b"no", "status": 406})
        self.assertEqual(r["status"], "rejected")
        self.assertFalse(r["ok"])
        self.assertEqual(self.neg.headers("a"),
                         {"accept-encoding": "identity"})

    def test_failure_does_not_affect_other_sites(self):
        self.neg.decode("bad", {"body": b"bad",
                                "headers": {"content-encoding": "gzip"}})
        self.assertIn("gzip", self.neg.headers("other")["accept-encoding"])

    def test_missing_decoder_marks_failure_and_never_offers_it(self):
        # 本机没有 br 解码器时：不声明 br，收到 br 响应标失败并留原始字节。
        original = neg._CODECS["br"]
        neg._CODECS["br"] = neg.Codec("br", None, None, "测试模拟缺失")
        try:
            neg_ = Negotiator()
            self.assertNotIn("br", offer_list())
            self.assertNotIn("br", neg_.headers("a")["accept-encoding"])
            r = neg_.decode("a", {"body": b"\x8b\x05",
                                  "headers": {"content-encoding": "br"}})
            self.assertEqual(r["status"], "decompress_failed")
            self.assertEqual(r["raw"], b"\x8b\x05")
            self.assertIn("不可用", r["reason"])
            # 其他站点和本机其它解码器不受影响
            self.assertIn("gzip", neg_.headers("b")["accept-encoding"])
        finally:
            neg._CODECS["br"] = original


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

    def test_locking_one_field_leaves_the_other_negotiating(self):
        # 只锁字符集时，压缩方式仍然自动协商。
        self.neg.override("a", charset="utf-8")
        self.assertEqual(self.neg.record("a")["locked_fields"], ["charset"])
        self.neg.decode("a", {"body": gz("正文"),
                              "headers": {"content-encoding": "gzip"}})
        self.assertEqual(self.neg.record("a")["accept_encoding"], "gzip")
        self.assertEqual(self.neg.record("a")["charset"], "utf-8")

    def test_override_survives_decompress_failure(self):
        self.neg.override("a", accept_encoding="gzip")
        self.neg.decode("a", {"body": b"bad",
                              "headers": {"content-encoding": "gzip"}})
        self.assertEqual(self.neg.record("a")["accept_encoding"], "gzip")
        self.assertFalse(self.neg.record("a")["fallback"])

    def test_unlock_triggers_reprobe(self):
        self.neg.override("a", accept_encoding="gzip")
        self.neg.unlock("a")  # 站点改版，解锁重探
        self.assertFalse(self.neg.record("a")["locked"])
        self.assertIn("gzip", self.neg.headers("a")["accept-encoding"])

    def test_override_rejects_unknown_codec(self):
        with self.assertRaises(ValueError):
            self.neg.override("a", accept_encoding="lzma")

    def test_override_rejects_unknown_charset(self):
        with self.assertRaises(ValueError):
            self.neg.override("a", charset="no-such-charset")

    def test_override_requires_a_value(self):
        with self.assertRaises(ValueError):
            self.neg.override("a")


class SelfcheckTest(unittest.TestCase):
    def test_selfcheck_ok(self):
        neg_ = Negotiator()
        report = neg_.selfcheck("a", {"body": gz("ok"),
                                      "headers": {"content-encoding": "gzip"}})
        self.assertTrue(report["ok"], report["detail"])
        self.assertIn("gzip", report["offered"])

    def test_selfcheck_without_response_runs_local_roundtrip(self):
        neg_ = Negotiator()
        report = neg_.selfcheck("fresh")
        self.assertTrue(report["ok"], report["detail"])
        self.assertIn("gzip", report["offered"])
        self.assertTrue(any(check["ok"] for check in report["checks"]))

    def test_selfcheck_failure_only_affects_that_site(self):
        neg_ = Negotiator()
        report = neg_.selfcheck("bad", {"body": b"bad",
                                        "headers": {"content-encoding": "gzip"}})
        self.assertFalse(report["ok"])
        self.assertEqual(neg_.headers("bad"), {"accept-encoding": "identity"})
        # 其他站点仍正常探测
        self.assertIn("gzip", neg_.headers("good")["accept-encoding"])

    def test_selfcheck_records_result(self):
        neg_ = Negotiator()
        neg_.selfcheck("fresh")
        self.assertIsNotNone(neg_.record("fresh")["selfcheck"])


class RecordTest(unittest.TestCase):
    def test_records_only_keep_latest_result(self):
        neg_ = Negotiator()
        neg_.decode("a", {"body": gz("一"),
                          "headers": {"content-encoding": "gzip"}})
        neg_.decode("a", {"body": "二".encode()})
        rec = neg_.record("a")
        self.assertEqual(rec["accept_encoding"], "identity")
        self.assertEqual(rec["last"]["content_encoding"], "identity")
        self.assertNotIn("history", rec)

    def test_records_lists_sites(self):
        neg_ = Negotiator()
        neg_.decode("a", {"body": b"x"})
        neg_.decode("b", {"body": b"y"})
        self.assertEqual(sorted(neg_.records()), ["a", "b"])

    def test_record_of_unknown_site(self):
        self.assertIsNone(Negotiator().record("nope")["accept_encoding"])

    def test_module_level_helpers_share_default_instance(self):
        clear_failed("mod-site")
        resp = {"body": b"bad", "headers": {"content-encoding": "gzip"}}
        decode("mod-site", resp)
        self.assertEqual(failed_responses("mod-site")[0]["body"], b"bad")
        self.assertEqual(record("mod-site")["last"]["status"],
                         "decompress_failed")
        self.assertIn("mod-site", records())
        unlock("mod-site")
        override("mod-site", accept_encoding="identity")
        self.assertTrue(record("mod-site")["locked"])
        selfcheck("mod-selfcheck")
        clear_failed("mod-site")
        self.assertEqual(failed_responses("mod-site"), [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "neg.json")

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def test_record_persists_across_instances(self):
        first = Negotiator(store_path=self.path)
        first.decode("shop", {"body": gz("正文"),
                              "headers": {"content-encoding": "gzip"}})
        first.override("pinned", accept_encoding="identity", charset="utf-8")
        second = Negotiator(store_path=self.path)
        self.assertEqual(second.headers("shop"), {"accept-encoding": "gzip"})
        self.assertTrue(second.record("pinned")["locked"])

    def test_store_errors_are_explicit(self):
        broken = os.path.join(self.dir, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(neg.StoreError):
            Negotiator(store_path=broken)


if __name__ == "__main__":
    unittest.main()
