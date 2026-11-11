"""Small JWT verification library used by internal services."""
import base64
import binascii
import hashlib
import hmac
import json
import re
import time

# Failure reasons surfaced via TokenError.reason:
#   "alg"       algorithm not in the explicitly allowed set (none is never allowed)
#   "kid"       kid not present in the key table
#   "signature" signature did not match any key registered for the kid
#   "expired"   token is past its exp
#   "not_yet"   token's nbf is still in the future
#   "format"    malformed token (structure, base64, JSON, claims, kid shape)
#   "keys"      key table itself is unusable (wrong types, missing fields)

DEFAULT_ALLOWED_ALGS = ("HS256", "RS256")
DEFAULT_LEEWAY = 30.0
MAX_KID_LENGTH = 128

_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+")


class TokenError(Exception):
    def __init__(self, reason: str, message: str = "") -> None:
        self.reason = reason
        super().__init__(message or reason)


def _b64d(value: str) -> bytes:
    if not value or _B64URL_RE.fullmatch(value) is None:
        raise TokenError("format", "invalid base64url segment")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise TokenError("format", "invalid base64url segment")


def _json_object(raw: bytes) -> dict:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise TokenError("format", "segment is not UTF-8 JSON")

    def no_duplicates(pairs):
        obj = {}
        for name, value in pairs:
            if name in obj:
                raise TokenError("format", "duplicate claim %r" % name)
            obj[name] = value
        return obj

    try:
        data = json.loads(text, object_pairs_hook=no_duplicates)
    except json.JSONDecodeError:
        raise TokenError("format", "segment is not valid JSON")
    if not isinstance(data, dict):
        raise TokenError("format", "header and payload must be JSON objects")
    return data


def _kid_from(header: dict) -> str:
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise TokenError("format", "kid must be a non-empty string")
    if len(kid) > MAX_KID_LENGTH:
        raise TokenError("format", "kid exceeds %d characters" % MAX_KID_LENGTH)
    return kid


def _key_candidates(keys, kid: str) -> list:
    if not isinstance(keys, dict):
        raise TokenError("keys", "keys must be a dict mapping kid to key or list of keys")
    if kid not in keys:
        raise TokenError("kid", "unknown kid %r" % kid)
    entry = keys[kid]
    if isinstance(entry, dict):
        return [entry]
    if isinstance(entry, (list, tuple)) and entry and all(isinstance(k, dict) for k in entry):
        return list(entry)
    raise TokenError("keys", "key table entry for %r is not a key or list of keys" % kid)


def _rsa_verify(message: bytes, signature: bytes, key: dict) -> bool:
    n, e = key.get("n"), key.get("e")
    if isinstance(n, bool) or isinstance(e, bool) or not isinstance(n, int) or not isinstance(e, int):
        raise TokenError("keys", "RS256 key must define integer n and e")
    if n <= 0 or e <= 0:
        raise TokenError("keys", "RS256 key must define positive n and e")
    digest = hashlib.sha256(message).digest()
    prefix = bytes.fromhex("3031300d060960864801650304020105000420")
    info = prefix + digest
    size = (n.bit_length() + 7) // 8
    if size < len(info) + 11 or len(signature) != size:
        return False
    expected = b"\x00\x01" + b"\xff" * (size - len(info) - 3) + b"\x00" + info
    actual = pow(int.from_bytes(signature, "big"), e, n)
    return hmac.compare_digest(actual.to_bytes(size, "big"), expected)


def _hs256_verify(message: bytes, signature: bytes, key: dict) -> bool:
    secret = key.get("secret")
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not isinstance(secret, (bytes, bytearray)):
        raise TokenError("keys", "HS256 key must define a bytes or string secret")
    expected = hmac.new(bytes(secret), message, hashlib.sha256).digest()
    return hmac.compare_digest(signature, expected)


def _numeric_claim(payload: dict, name: str):
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TokenError("format", "claim %r must be a number" % name)
    return float(value)


def verify_token(token: str, keys, now=None, *, allowed_algs=None, leeway=DEFAULT_LEEWAY) -> dict:
    """Verify a JWT and return its payload.

    ``keys`` maps a kid to one key dict or, during rotation, a list of key
    dicts that are all accepted (grace window). ``allowed_algs`` explicitly
    enables algorithms (default HS256 + RS256); "none" is always rejected.
    ``leeway`` is the clock-skew tolerance in seconds applied to nbf.
    """
    if allowed_algs is None:
        allowed_algs = DEFAULT_ALLOWED_ALGS
    allowed = {alg for alg in allowed_algs if alg != "none"}
    leeway = float(leeway)
    if leeway < 0:
        raise TokenError("keys", "leeway must not be negative")

    if not isinstance(token, str):
        raise TokenError("format", "token must be a string")
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise TokenError("format", "token must have three non-empty segments")
    header = _json_object(_b64d(parts[0]))
    payload = _json_object(_b64d(parts[1]))
    signature = _b64d(parts[2])

    alg = header.get("alg")
    if not isinstance(alg, str) or alg not in allowed:
        raise TokenError("alg", "algorithm %r is not allowed" % (alg,))
    kid = _kid_from(header)

    message = (parts[0] + "." + parts[1]).encode("ascii")
    verifier = _hs256_verify if alg == "HS256" else _rsa_verify
    if not any(verifier(message, signature, key) for key in _key_candidates(keys, kid)):
        raise TokenError("signature", "signature does not match any key for kid %r" % kid)

    clock = float(time.time() if now is None else now)
    exp = _numeric_claim(payload, "exp")
    if exp is not None and clock > exp:
        raise TokenError("expired", "token expired at %r" % exp)
    nbf = _numeric_claim(payload, "nbf")
    if nbf is not None and clock < nbf - leeway:
        raise TokenError("not_yet", "token not valid before %r" % nbf)
    return payload
