"""令牌保管库：原值按版本密钥加密保存，凭令牌还原。

令牌格式 v<密钥版本>.<令牌ID>，轮换密钥后历史令牌仍按原版本还原。
任何校验失败都抛 VaultError，绝不返回部分原值。

注：此处用 HMAC 密钥流做演示级加密，生产环境应替换为 KMS/AES-GCM。
"""
import base64
import hashlib
import hmac
import itertools


class VaultError(Exception):
    pass


def _keystream(key, token_id, field, n):
    out = b""
    counter = 0
    while len(out) < n:
        out += hmac.new(
            key, f"{token_id}:{field}:{counter}".encode(), hashlib.sha256
        ).digest()
        counter += 1
    return out[:n]


def _xor(data, stream):
    return bytes(a ^ b for a, b in zip(data, stream))


class Vault:
    def __init__(self, keyring):
        self.keyring = keyring
        self._seq = itertools.count(1)
        self._store = {}  # token_id -> {table, key_version, fields}

    def seal(self, table, values):
        """把 {字段: 原值} 加密入库，返回令牌。"""
        version = self.keyring.current_version
        key = self.keyring.get(version)
        token_id = f"tok-{next(self._seq)}"
        fields = {}
        for name, value in values.items():
            data = str(value).encode("utf-8")
            ct = _xor(data, _keystream(key, token_id, name, len(data)))
            tag = hmac.new(
                key, f"{token_id}:{name}".encode() + ct, hashlib.sha256
            ).hexdigest()
            fields[name] = {
                "ct": base64.urlsafe_b64encode(ct).decode(),
                "tag": tag,
            }
        self._store[token_id] = {
            "table": table,
            "key_version": version,
            "fields": fields,
        }
        return f"v{version}.{token_id}"

    def _parse(self, token):
        try:
            ver, token_id = str(token).split(".", 1)
            version = int(ver.lstrip("v"))
        except (ValueError, AttributeError):
            raise VaultError("malformed token")
        if token_id not in self._store:
            raise VaultError("unknown token")
        return version, token_id

    def table_of(self, token):
        """不解密，仅查令牌归属的表（用于授权校验）。"""
        _, token_id = self._parse(token)
        return self._store[token_id]["table"]

    def open(self, token, fields=None):
        """校验并还原全部（或指定）字段；任一字段校验失败即整体失败。"""
        version, token_id = self._parse(token)
        entry = self._store[token_id]
        try:
            key = self.keyring.get(version)
        except KeyError:
            raise VaultError(f"unknown key version: {version}")
        wanted = list(fields) if fields else list(entry["fields"])
        out = {}
        for name in wanted:
            rec = entry["fields"].get(name)
            if rec is None:
                raise VaultError(f"unknown field: {name}")
            ct = base64.urlsafe_b64decode(rec["ct"].encode())
            tag = hmac.new(
                key, f"{token_id}:{name}".encode() + ct, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(tag, rec["tag"]):
                raise VaultError("integrity check failed")
            out[name] = _xor(ct, _keystream(key, token_id, name, len(ct))).decode(
                "utf-8"
            )
        return out
