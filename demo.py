import base64
import hashlib
import hmac
import json

from tokenforge import verify_token


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


header = b64(json.dumps({"alg": "HS256", "kid": "main"}, separators=(",", ":")).encode())
body = b64(json.dumps({"sub": "demo", "exp": 4102444800}, separators=(",", ":")).encode())
message = f"{header}.{body}".encode()
sig = b64(hmac.new(b"secret", message, hashlib.sha256).digest())
print(verify_token(f"{header}.{body}.{sig}", {"main": {"secret": b"secret"}}))
