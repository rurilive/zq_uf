"""版本化密钥与密码学原语（仅使用 Python 标准库）。

设计要点：
- Keyring 保存若干版本的主密钥，rotate 产生新版本，current_version 单调递增；
- 每个版本派生出独立的加密子密钥（域 "enc"）和 MAC 子密钥（域 "mac"）；
- 机密性用 ChaCha 风格的 HMAC-SHA256 密钥流异或（标准库没有 AES，也不引入
  第三方依赖）；完整性用 HMAC-SHA256，二者组合成 authenticate-then-decrypt；
- 所有比较走 hmac.compare_digest，杜绝计时侧信道。
"""

import base64
import hmac
import os
import struct
import hashlib


def _derive_key(master_key, domain):
    """按域派生子密钥，避免加密和 MAC 复用同一把密钥。"""
    return hmac.new(master_key, b"tabledesens|" + domain.encode(),
                    hashlib.sha256).digest()


def _keystream(enc_key, nonce, length):
    """生成 length 字节的密钥流：HMAC(key, nonce || counter)。"""
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            enc_key, nonce + struct.pack(">Q", counter),
            hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _xor(data, key_stream):
    return bytes(a ^ b for a, b in zip(data, key_stream))


def b64e(raw):
    """无填充 URL 安全 base64 编码。"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64d(text):
    """b64e 的逆运算，自动补回填充。"""
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class Keyring:
    """多版本密钥环。旧版本保留用于解密历史令牌，当前版本用于新加密。"""

    def __init__(self, master_key=None):
        # version -> 主密钥
        self._keys = {}
        self._current = 0
        if master_key is not None:
            self.rotate(master_key)

    @property
    def current_version(self):
        return self._current

    def rotate(self, master_key=None):
        """登记新版本密钥；未显式提供则生成 32 字节随机密钥。"""
        if master_key is None:
            master_key = os.urandom(32)
        elif isinstance(master_key, str):
            master_key = master_key.encode("utf-8")
        self._current += 1
        self._keys[self._current] = bytes(master_key)
        return self._current

    def _require(self, version):
        if version not in self._keys:
            raise KeyError("unknown key version %r" % version)
        return self._keys[version]

    def seal(self, plaintext):
        """用当前版本密钥加密，返回 (密文包 dict, 密钥版本)。

        密文包结构：{"v": 版本, "nonce": b64, "ct": b64, "tag": b64}。
        """
        version = self._current
        return self.seal_with(version, plaintext), version

    def seal_with(self, version, plaintext):
        master_key = self._require(version)
        enc_key = _derive_key(master_key, "enc")
        mac_key = _derive_key(master_key, "mac")
        nonce = os.urandom(16)
        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")
        ciphertext = _xor(plaintext, _keystream(enc_key, nonce, len(plaintext)))
        tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
        return {"v": version, "nonce": b64e(nonce),
                "ct": b64e(ciphertext), "tag": b64e(tag)}

    def open(self, envelope):
        """验签并解密密文包，返回明文字节。版本不存在或验签失败均报错。"""
        version = envelope["v"]
        master_key = self._require(version)
        enc_key = _derive_key(master_key, "enc")
        mac_key = _derive_key(master_key, "mac")
        nonce = b64d(envelope["nonce"])
        ciphertext = b64d(envelope["ct"])
        expected_tag = b64d(envelope["tag"])
        actual_tag = hmac.new(
            mac_key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(expected_tag, actual_tag):
            raise ValueError("ciphertext authentication failed")
        return _xor(ciphertext, _keystream(enc_key, nonce, len(ciphertext)))

    def sign(self, message):
        """用当前版本的 MAC 子密钥签名，返回 (hex 签名, 版本)。"""
        master_key = self._keys[self._current]
        mac_key = _derive_key(master_key, "mac")
        return hmac.new(mac_key, message, hashlib.sha256).hexdigest(), \
            self._current

    def verify(self, message, signature, version):
        """用指定版本密钥验签，常量时间比较，返回 True/False。"""
        master_key = self._require(version)
        mac_key = _derive_key(master_key, "mac")
        expected = hmac.new(mac_key, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def pseudonym(self, message, version=None):
        """用指定版本（默认当前版本）主密钥生成不可逆假名的十六进制摘要。"""
        version = version or self._current
        master_key = self._require(version)
        return hmac.new(master_key, message,
                        hashlib.sha256).hexdigest()
