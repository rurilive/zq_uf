import re
import unittest

from signparam import SignError, sign

# 与 demo.py 相同的固定参数和规则，断言值必须和 demo 输出一致。
DEMO_RULE = {
    "sort": "asc",
    "quote": True,
    "algo": "hmac-sha256",
    "secret": "demo-secret",
    "timestamp": {"param": "ts", "format": "epoch"},
    "nonce": {"param": "nonce", "length": 16},
}
DEMO_PARAMS = {
    "appid": "demo",
    "q": "键盘",
    "ts": "1700000000",
    "nonce": "abcdef1234567890",
}
DEMO_SIGN = "61030f9b7e46aaf56c1955863add59579d748cccc444267e87e7f0a449642740"


class SignTest(unittest.TestCase):
    def test_stable_for_same_params(self):
        params = {"b": "2", "a": "1"}
        self.assertEqual(sign(params)["sign"], sign({"a": "1", "b": "2"})["sign"])

    def test_raw_uses_sorted_keys(self):
        self.assertEqual(sign({"b": "2", "a": "1"})["raw"], "a=1&b=2")

    def test_missing_key_changes_sign(self):
        self.assertNotEqual(sign({"a": "1"})["sign"], sign({"a": "1", "b": "2"})["sign"])


class SortTest(unittest.TestCase):
    def test_desc_sort(self):
        self.assertEqual(
            sign({"a": "1", "b": "2"}, {"sort": "desc"})["raw"], "b=2&a=1")

    def test_none_sort_keeps_insertion_order(self):
        self.assertEqual(
            sign({"b": "2", "a": "1"}, {"sort": "none"})["raw"], "b=2&a=1")

    def test_custom_separators(self):
        rule = {"kv_sep": ":", "pair_sep": ";"}
        self.assertEqual(sign({"b": "2", "a": "1"}, rule)["raw"], "a:1;b:2")

    def test_bad_sort_raises(self):
        with self.assertRaises(SignError):
            sign({"a": "1"}, {"sort": "random"})


class EncodingTest(unittest.TestCase):
    def test_quote_encodes_value(self):
        raw = sign({"q": "键 盘"}, {"quote": True})["raw"]
        self.assertEqual(raw, "q=%E9%94%AE%20%E7%9B%98")

    def test_no_quote_keeps_value(self):
        self.assertEqual(sign({"q": "键盘"})["raw"], "q=键盘")

    def test_encoding_affects_digest(self):
        params = {"q": "键盘"}
        utf8 = sign(params, {"encoding": "utf-8"})["sign"]
        gbk = sign(params, {"encoding": "gbk"})["sign"]
        self.assertNotEqual(utf8, gbk)

    def test_unknown_encoding_raises(self):
        with self.assertRaises(SignError):
            sign({"a": "1"}, {"encoding": "no-such-codec"})


class TimestampTest(unittest.TestCase):
    RULE = {"timestamp": {"param": "ts", "format": "epoch"}}

    def test_given_timestamp_is_validated_and_used(self):
        result = sign({"a": "1", "ts": "1700000000"}, self.RULE)
        self.assertEqual(result["snapshot"]["ts"], "1700000000")

    def test_missing_timestamp_is_generated(self):
        result = sign({"a": "1"}, self.RULE)
        self.assertRegex(result["snapshot"]["ts"], r"^\d{10}$")

    def test_bad_timestamp_raises(self):
        with self.assertRaises(SignError):
            sign({"a": "1", "ts": "not-a-ts"}, self.RULE)

    def test_epoch_ms_format(self):
        rule = {"timestamp": {"param": "ts", "format": "epoch_ms"}}
        result = sign({"a": "1"}, rule)
        self.assertRegex(result["snapshot"]["ts"], r"^\d{13}$")
        with self.assertRaises(SignError):
            sign({"a": "1", "ts": "1700000000"}, rule)

    def test_iso8601_format(self):
        rule = {"timestamp": {"param": "ts", "format": "iso8601"}}
        result = sign({"a": "1", "ts": "2023-11-14T22:13:20Z"}, rule)
        self.assertEqual(result["snapshot"]["ts"], "2023-11-14T22:13:20Z")
        with self.assertRaises(SignError):
            sign({"a": "1", "ts": "2023-13-40T25:61:61Z"}, rule)

    def test_bad_timestamp_spec_raises(self):
        with self.assertRaises(SignError):
            sign({"a": "1"}, {"timestamp": {"param": "ts", "format": "unix"}})
        with self.assertRaises(SignError):
            sign({"a": "1"}, {"timestamp": {"param": "ts"}})


class NonceTest(unittest.TestCase):
    RULE = {"nonce": {"param": "nonce", "length": 16}}

    def test_missing_nonce_is_generated(self):
        result = sign({"a": "1"}, self.RULE)
        nonce = result["snapshot"]["nonce"]
        self.assertEqual(len(nonce), 16)
        self.assertRegex(nonce, r"^[A-Za-z0-9]+$")

    def test_bad_nonce_raises(self):
        with self.assertRaises(SignError):
            sign({"a": "1", "nonce": "short"}, self.RULE)
        with self.assertRaises(SignError):
            sign({"a": "1", "nonce": "!" * 16}, self.RULE)

    def test_bad_nonce_spec_raises(self):
        with self.assertRaises(SignError):
            sign({"a": "1"}, {"nonce": {"param": "nonce", "length": 0}})
        with self.assertRaises(SignError):
            sign({"a": "1"}, {"nonce": {"param": "nonce", "length": 65}})


class AlgoTest(unittest.TestCase):
    def test_md5_known_value(self):
        self.assertEqual(
            sign({"b": "2", "a": "1"})["sign"],
            "ed04c91cf6f6ab5a01a31c0295c5da34")

    def test_sha256_known_value(self):
        self.assertEqual(
            sign({"b": "2", "a": "1"}, {"algo": "sha256"})["sign"],
            "8e85be58c1c372ac29fe7bfa80d8ddcbd04a4032c7b51c1c026d67c55b1ab23f")

    def test_hmac_sha256_matches_demo(self):
        result = sign(DEMO_PARAMS, DEMO_RULE)
        self.assertEqual(result["sign"], DEMO_SIGN)
        self.assertEqual(
            result["raw"],
            "appid=demo&nonce=abcdef1234567890"
            "&q=%E9%94%AE%E7%9B%98&ts=1700000000")

    def test_hmac_requires_secret(self):
        with self.assertRaises(SignError):
            sign({"a": "1"}, {"algo": "hmac-sha256"})

    def test_plain_hash_rejects_secret(self):
        with self.assertRaises(SignError):
            sign({"a": "1"}, {"algo": "md5", "secret": "x"})

    def test_unknown_algo_raises(self):
        with self.assertRaises(SignError):
            sign({"a": "1"}, {"algo": "crc32"})
        with self.assertRaises(SignError):
            sign({"a": "1"}, {"algo": "hmac-crc32", "secret": "x"})


class ReplayTest(unittest.TestCase):
    def test_snapshot_replays_same_sign(self):
        result = sign({"a": "1"}, {
            "algo": "hmac-sha256",
            "secret": "k",
            "timestamp": {"param": "ts", "format": "epoch"},
            "nonce": {"param": "nonce", "length": 8},
        })
        replay = sign(result["snapshot"], {
            "algo": "hmac-sha256",
            "secret": "k",
            "timestamp": {"param": "ts", "format": "epoch"},
            "nonce": {"param": "nonce", "length": 8},
        })
        self.assertEqual(replay["sign"], result["sign"])

    def test_snapshot_contains_generated_fields(self):
        result = sign({"a": "1"}, {
            "timestamp": {"param": "ts", "format": "epoch"},
            "nonce": {"param": "nonce", "length": 8},
        })
        self.assertEqual(set(result["snapshot"]), {"a", "ts", "nonce"})


class ParamValidationTest(unittest.TestCase):
    def test_non_dict_params_raises(self):
        with self.assertRaises(SignError):
            sign([("a", "1")])

    def test_empty_params_raises(self):
        with self.assertRaises(SignError):
            sign({})

    def test_empty_key_raises(self):
        with self.assertRaises(SignError):
            sign({"": "1"})

    def test_empty_value_raises(self):
        with self.assertRaises(SignError):
            sign({"a": ""})

    def test_bool_value_raises(self):
        with self.assertRaises(SignError):
            sign({"a": True})

    def test_unsupported_value_type_raises(self):
        with self.assertRaises(SignError):
            sign({"a": ["1"]})

    def test_int_value_is_stringified(self):
        self.assertEqual(sign({"a": 1})["raw"], "a=1")

    def test_unknown_rule_field_raises(self):
        with self.assertRaises(SignError):
            sign({"a": "1"}, {"sgin": "typo"})

    def test_non_dict_rule_raises(self):
        with self.assertRaises(SignError):
            sign({"a": "1"}, "md5")


if __name__ == "__main__":
    unittest.main()
