import base64
import hashlib
import hmac
import json
import unittest

from tokenforge import TokenError, verify_token


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def build(payload, kid="main", secret=b"secret"):
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    head = b64(json.dumps(header, separators=(",", ":")).encode())
    body = b64(json.dumps(payload, separators=(",", ":")).encode())
    message = f"{head}.{body}".encode("ascii")
    sig = b64(hmac.new(secret, message, hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


class VerifyTest(unittest.TestCase):
    def test_valid(self):
        value = build({"sub": "u1", "exp": 9999999999})
        payload = verify_token(value, {"main": {"secret": b"secret"}})
        self.assertEqual(payload["sub"], "u1")

    def test_expired(self):
        value = build({"sub": "u1", "exp": 5})
        with self.assertRaises(TokenError):
            verify_token(value, {"main": {"secret": b"secret"}}, now=10)

    def test_bad_signature(self):
        value = build({"sub": "u1", "exp": 9999999999})
        with self.assertRaises(TokenError):
            verify_token(value, {"main": {"secret": b"other"}})


KEYS = {"main": {"secret": b"secret"}}

RSA_N = 126698271048503919302727273680579780164020045342129187552456704499029779379638150203176578823518265793968636406791399462441113922676004517674881954087242157453608842556007139074774174018178886709415045048500653258888052473439806587579275314638213589107622449397696296886780065129296183961742095830045363301429
RSA_E = 65537
RSA_D = 119976420972430126980595001367422694920107481482712626142567756858107452405516633376705349371920343854215078129994876949495636511303124042402050354313658649097606042575822069934908181375765911930585381570813285697925178937337188337978356227929380792438985933759893784396283675020169131431883167481642163011073


def raw_token(head: str, body: str, sig: str = "") -> str:
    return f"{head}.{body}.{sig}"


def json_segment(obj) -> str:
    return b64(json.dumps(obj, separators=(",", ":")).encode())


def hs256_token(payload, header=None, secret=b"secret"):
    header = {"alg": "HS256", "kid": "main"} if header is None else header
    head, body = json_segment(header), json_segment(payload)
    message = f"{head}.{body}".encode("ascii")
    return raw_token(head, body, b64(hmac.new(secret, message, hashlib.sha256).digest()))


def rs256_token(payload, kid="main", d=RSA_D, n=RSA_N):
    head, body = json_segment({"alg": "RS256", "kid": kid}), json_segment(payload)
    message = f"{head}.{body}".encode("ascii")
    digest = hashlib.sha256(message).digest()
    prefix = bytes.fromhex("3031300d060960864801650304020105000420")
    info = prefix + digest
    size = (n.bit_length() + 7) // 8
    encoded = b"\x00\x01" + b"\xff" * (size - len(info) - 3) + b"\x00" + info
    sig = pow(int.from_bytes(encoded, "big"), d, n).to_bytes(size, "big")
    return raw_token(head, body, b64(sig))


def reason_of(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except TokenError as exc:
        return exc.reason
    return None


class AlgTest(unittest.TestCase):
    def test_none_alg_rejected(self):
        token = raw_token(json_segment({"alg": "none", "kid": "main"}),
                          json_segment({"sub": "u1", "admin": True}), b64(b"x"))
        self.assertEqual(reason_of(verify_token, token, KEYS), "alg")

    def test_none_rejected_even_if_explicitly_listed(self):
        token = raw_token(json_segment({"alg": "none", "kid": "main"}),
                          json_segment({"sub": "u1"}), b64(b"x"))
        self.assertEqual(
            reason_of(verify_token, token, KEYS, allowed_algs=["none", "HS256"]), "alg")

    def test_alg_not_in_allowed_set(self):
        token = hs256_token({"sub": "u1", "exp": 9999999999})
        self.assertEqual(
            reason_of(verify_token, token, KEYS, allowed_algs=["RS256"]), "alg")

    def test_alg_checked_before_kid(self):
        token = raw_token(json_segment({"alg": "none"}), json_segment({"sub": "u1"}), b64(b"x"))
        self.assertEqual(reason_of(verify_token, token, {}), "alg")

    def test_missing_alg(self):
        token = raw_token(json_segment({"kid": "main"}), json_segment({"sub": "u1"}), b64(b"x"))
        self.assertEqual(reason_of(verify_token, token, KEYS), "alg")


class KidTest(unittest.TestCase):
    def test_unknown_kid(self):
        token = hs256_token({"sub": "u1"}, header={"alg": "HS256", "kid": "ghost"})
        self.assertEqual(reason_of(verify_token, token, KEYS), "kid")

    def test_traversal_kid_is_plain_lookup(self):
        token = hs256_token({"sub": "u1"}, header={"alg": "HS256", "kid": "../outside"})
        self.assertEqual(reason_of(verify_token, token, KEYS), "kid")

    def test_empty_kid(self):
        token = hs256_token({"sub": "u1"}, header={"alg": "HS256", "kid": ""})
        self.assertEqual(reason_of(verify_token, token, KEYS), "format")

    def test_missing_kid(self):
        token = hs256_token({"sub": "u1"}, header={"alg": "HS256"})
        self.assertEqual(reason_of(verify_token, token, KEYS), "format")

    def test_non_string_kid(self):
        token = hs256_token({"sub": "u1"}, header={"alg": "HS256", "kid": 7})
        self.assertEqual(reason_of(verify_token, token, KEYS), "format")

    def test_overlong_kid(self):
        token = hs256_token({"sub": "u1"}, header={"alg": "HS256", "kid": "k" * 129})
        self.assertEqual(reason_of(verify_token, token, KEYS), "format")

    def test_max_length_kid_accepted(self):
        kid = "k" * 128
        token = hs256_token({"sub": "u1", "exp": 9999999999},
                            header={"alg": "HS256", "kid": kid})
        payload = verify_token(token, {kid: {"secret": b"secret"}})
        self.assertEqual(payload["sub"], "u1")


class SignatureTest(unittest.TestCase):
    def test_reason_is_signature(self):
        token = hs256_token({"sub": "u1"}, secret=b"other")
        self.assertEqual(reason_of(verify_token, token, KEYS), "signature")

    def test_rs256_valid(self):
        token = rs256_token({"sub": "u1", "exp": 9999999999})
        payload = verify_token(token, {"main": {"n": RSA_N, "e": RSA_E}})
        self.assertEqual(payload["sub"], "u1")

    def test_rs256_bad_signature(self):
        token = rs256_token({"sub": "u1"})
        head, body, _ = token.split(".")
        forged = raw_token(head, body, b64(b"\x01" * 128))
        self.assertEqual(
            reason_of(verify_token, forged, {"main": {"n": RSA_N, "e": RSA_E}}),
            "signature")


class RotationTest(unittest.TestCase):
    old = {"secret": b"old-secret"}
    new = {"secret": b"new-secret"}

    def test_grace_window_accepts_old_and_new(self):
        keys = {"main": [self.old, self.new]}
        for secret in (b"old-secret", b"new-secret"):
            token = hs256_token({"sub": "u1", "exp": 9999999999}, secret=secret)
            self.assertEqual(verify_token(token, keys)["sub"], "u1")

    def test_grace_window_rejects_unknown_key(self):
        keys = {"main": [self.old, self.new]}
        token = hs256_token({"sub": "u1"}, secret=b"third")
        self.assertEqual(reason_of(verify_token, token, keys), "signature")

    def test_after_rotation_old_tokens_fail(self):
        token = hs256_token({"sub": "u1"}, secret=b"old-secret")
        self.assertEqual(
            reason_of(verify_token, token, {"main": self.new}), "signature")

    def test_tuple_of_keys_accepted(self):
        token = hs256_token({"sub": "u1", "exp": 9999999999}, secret=b"old-secret")
        payload = verify_token(token, {"main": (self.old, self.new)})
        self.assertEqual(payload["sub"], "u1")


class ExpiryTest(unittest.TestCase):
    def test_fractional_now_past_exp_rejected(self):
        token = hs256_token({"sub": "u1", "exp": 10})
        self.assertEqual(reason_of(verify_token, token, KEYS, now=10.9), "expired")

    def test_fractional_now_before_exp_accepted(self):
        token = hs256_token({"sub": "u1", "exp": 10})
        self.assertEqual(verify_token(token, KEYS, now=9.9)["sub"], "u1")

    def test_fractional_exp(self):
        token = hs256_token({"sub": "u1", "exp": 10.5})
        self.assertEqual(verify_token(token, KEYS, now=10.2)["sub"], "u1")
        self.assertEqual(reason_of(verify_token, token, KEYS, now=10.6), "expired")

    def test_exp_equal_to_now_accepted(self):
        token = hs256_token({"sub": "u1", "exp": 10})
        self.assertEqual(verify_token(token, KEYS, now=10)["sub"], "u1")


class NotBeforeTest(unittest.TestCase):
    def test_future_nbf_rejected(self):
        token = hs256_token({"sub": "u1", "nbf": 1000})
        self.assertEqual(reason_of(verify_token, token, KEYS, now=100), "not_yet")

    def test_nbf_within_default_leeway_accepted(self):
        token = hs256_token({"sub": "u1", "nbf": 1000})
        self.assertEqual(verify_token(token, KEYS, now=970.5)["sub"], "u1")

    def test_configurable_leeway(self):
        token = hs256_token({"sub": "u1", "nbf": 1000})
        self.assertEqual(
            verify_token(token, KEYS, now=100, leeway=901)["sub"], "u1")
        self.assertEqual(
            reason_of(verify_token, token, KEYS, now=100, leeway=899), "not_yet")

    def test_zero_leeway_is_strict(self):
        token = hs256_token({"sub": "u1", "nbf": 1000})
        self.assertEqual(
            reason_of(verify_token, token, KEYS, now=999.9, leeway=0), "not_yet")

    def test_past_nbf_accepted(self):
        token = hs256_token({"sub": "u1", "nbf": 1000})
        self.assertEqual(verify_token(token, KEYS, now=1000)["sub"], "u1")


class FormatTest(unittest.TestCase):
    def check(self, token):
        self.assertEqual(reason_of(verify_token, token, KEYS), "format")

    def test_not_a_string(self):
        self.assertEqual(reason_of(verify_token, None, KEYS), "format")
        self.assertEqual(reason_of(verify_token, b"a.b.c", KEYS), "format")

    def test_wrong_segment_count(self):
        self.check("a.b")
        self.check("a.b.c.d")
        self.check("")

    def test_empty_segments(self):
        self.check("a..b")
        self.check(".b.c")
        self.check("a.b.")

    def test_invalid_base64(self):
        self.check(raw_token("!!!", json_segment({"sub": "u1"}), "sig"))
        self.check(raw_token(json_segment({"alg": "HS256", "kid": "main"}),
                             json_segment({"sub": "u1"}), "@@"))
        self.check(raw_token("abc=d", json_segment({"sub": "u1"}), "sig"))

    def test_non_json_segments(self):
        self.check(raw_token(b64(b"not json"), json_segment({"sub": "u1"}), "sig"))
        self.check(raw_token(b64(b"\xff\xfe{}"), json_segment({"sub": "u1"}), "sig"))

    def test_non_object_segments(self):
        self.check(raw_token(json_segment(["alg"]), json_segment({"sub": "u1"}), "sig"))
        self.check(raw_token(json_segment({"alg": "HS256", "kid": "main"}),
                             json_segment([1, 2]), "sig"))
        self.check(raw_token(json_segment("str"), json_segment({"sub": "u1"}), "sig"))

    def test_duplicate_header_claims(self):
        head = b64(b'{"alg":"HS256","alg":"none","kid":"main"}')
        self.check(raw_token(head, json_segment({"sub": "u1"}), "sig"))

    def test_duplicate_payload_claims(self):
        body = b64(b'{"sub":"a","sub":"b"}')
        token = raw_token(json_segment({"alg": "HS256", "kid": "main"}), body, "sig")
        self.check(token)

    def test_non_numeric_exp(self):
        self.check(hs256_token({"sub": "u1", "exp": "soon"}))
        self.check(hs256_token({"sub": "u1", "exp": True}))

    def test_non_numeric_nbf(self):
        self.check(hs256_token({"sub": "u1", "nbf": "later"}))


class KeyTableTest(unittest.TestCase):
    def test_keys_not_a_dict(self):
        token = hs256_token({"sub": "u1"})
        self.assertEqual(reason_of(verify_token, token, "/tmp/keys"), "keys")

    def test_entry_wrong_type(self):
        token = hs256_token({"sub": "u1"})
        self.assertEqual(reason_of(verify_token, token, {"main": "secret"}), "keys")

    def test_entry_empty_list(self):
        token = hs256_token({"sub": "u1"})
        self.assertEqual(reason_of(verify_token, token, {"main": []}), "keys")

    def test_hs256_key_without_secret(self):
        token = hs256_token({"sub": "u1"})
        self.assertEqual(reason_of(verify_token, token, {"main": {}}), "keys")

    def test_hs256_string_secret_accepted(self):
        token = hs256_token({"sub": "u1", "exp": 9999999999}, secret=b"secret")
        payload = verify_token(token, {"main": {"secret": "secret"}})
        self.assertEqual(payload["sub"], "u1")

    def test_rs256_key_missing_params(self):
        token = rs256_token({"sub": "u1"})
        self.assertEqual(reason_of(verify_token, token, {"main": {"n": RSA_N}}), "keys")
        self.assertEqual(
            reason_of(verify_token, token, {"main": {"n": "x", "e": RSA_E}}), "keys")

    def test_negative_leeway(self):
        token = hs256_token({"sub": "u1"})
        self.assertEqual(reason_of(verify_token, token, KEYS, leeway=-1), "keys")


if __name__ == "__main__":
    unittest.main()
