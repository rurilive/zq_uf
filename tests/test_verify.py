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
