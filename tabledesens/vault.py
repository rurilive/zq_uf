"""还原令牌库：保管 MASK 字段的原值，发放/校验/吊销令牌。

令牌是自包含的签名串（不落库也能验证真伪），但吊销名单由令牌库集中
维护，吊销通过内存集合即时生效；同一字段重新签发会作废旧令牌（新 nonce、
新 token id），旧令牌自动进入吊销名单。

令牌明文布局（JSON canonical，键排序、无空格）::

    {"f": 字段, "id": 令牌id, "n": 血缘节点, "r": 密文包, "s": 签名, "v": 密钥版本}
"""

import json
import uuid

from .crypto import b64e, b64d
from .errors import TokenError, TokenRevokedError


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


class TokenVault:
    """令牌签发、解析与吊销；原值以版本化密钥加密后保存在内存。"""

    def __init__(self, keyring):
        self._keyring = keyring
        # token id -> 密文包（解析令牌后据此取回原值）
        self._records = {}
        # 已吊销的 token id 集合；吊销立即生效
        self._revoked = set()
        # (节点, 字段) -> 当前有效令牌的 token id，重新签发即作废旧 id
        self._active = {}

    @property
    def keyring(self):
        return self._keyring

    def issue(self, node, field, value):
        """为一个 MASK 字段的原值签发还原令牌，返回令牌字符串。

        同一 (节点, 字段) 重新签发时，旧令牌自动吊销，立即失效。
        """
        plaintext = json.dumps(value, ensure_ascii=False)
        envelope, version = self._keyring.seal(plaintext)
        token_id = uuid.uuid4().hex
        payload = {"f": field, "id": token_id, "n": node,
                   "r": envelope, "v": version}
        message = _canonical(payload)
        signature, _sig_version = self._keyring.sign(message)
        payload["s"] = signature
        token = b64e(_canonical(payload))
        self._records[token_id] = envelope

        scope = (node, field)
        previous = self._active.get(scope)
        if previous is not None:
            # 旧令牌作废：记录保留以便审计追溯，但不再能通过校验
            self._revoked.add(previous)
        self._active[scope] = token_id
        return token

    def parse(self, token, allow_revoked=False):
        """解析并验签令牌，返回 payload；伪造或版本未知一律拒绝。

        默认情况下已吊销令牌抛 TokenRevokedError；allow_revoked=True 时
        放行（供 revoke 等管理操作使用，业务还原路径永远走默认分支）。
        """
        if not token or not isinstance(token, str):
            raise TokenError("missing reveal token")
        try:
            payload = json.loads(b64d(token).decode("utf-8"))
            token_id = payload["id"]
            version = payload["v"]
            signature = payload.pop("s")
        except (ValueError, KeyError, TypeError) as exc:
            raise TokenError("malformed token: %s" % exc)

        if not self._keyring.verify(_canonical(payload), signature, version):
            raise TokenError("token signature verification failed")
        if token_id in self._revoked and not allow_revoked:
            raise TokenRevokedError("token %s has been revoked" % token_id)
        if token_id not in self._records:
            # 令牌签名合法但记录不在本库（例如别的环境签发的）
            raise TokenError("token %s is not registered in this vault"
                             % token_id)
        payload["s"] = signature
        return payload

    def fetch(self, payload):
        """按解析后的 payload 取回原值，吊销状态会再次检查。"""
        token_id = payload["id"]
        if token_id in self._revoked:
            raise TokenRevokedError("token %s has been revoked" % token_id)
        envelope = self._records.get(token_id)
        if envelope is None:
            raise TokenError("token %s record not found" % token_id)
        raw = self._keyring.open(envelope)
        return json.loads(raw.decode("utf-8"))

    def revoke(self, token):
        """吊销令牌：接受令牌字符串或已解析的 payload，立即且永久失效。

        吊销操作幂等——重复吊销同一令牌不报错。
        """
        payload = (self.parse(token, allow_revoked=True)
                   if isinstance(token, str) else token)
        token_id = payload["id"]
        self._revoked.add(token_id)
        return token_id

    def is_revoked(self, token):
        """令牌是否已吊销（非法令牌按已失效处理，返回 True）。"""
        try:
            payload = self.parse(token, allow_revoked=True)
        except TokenError:
            return True
        return payload["id"] in self._revoked
