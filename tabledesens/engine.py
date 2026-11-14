"""脱敏引擎：按血缘策略执行 mask / 受控 reveal。

典型用法::

    lineage = Lineage(edges=[("ods.user", "dwd.user")],
                      rules={"ods.user": {"phone": MASK}})
    engine = DesensEngine(lineage, node="dwd.user",
                          default_strategy=MASK)
    result = engine.mask(row, operator="etl")
    restored = engine.reveal(result.row, result.tokens,
                             operator="analyst")

权限口径：
- mask 是安全侧操作，不设权限，谁执行都只输出脱敏结果；
- reveal 必须显式持有该节点（或通配 "*"）的还原授权，匿名一律拒绝；
- 还原同时校验令牌（签名、归属节点、吊销状态）、权限、密钥版本，三者
  缺一不可，所有尝试——包括被拒绝的——都写审计。
"""

from .audit import AuditLog
from .crypto import Keyring
from .errors import (PermissionDeniedError, TokenError,
                     TokenRevokedError)
from .policy import (DEFAULT_STRATEGY, NODE_DEFAULT, NONE, MASK,
                     HASH, REDACT, validate_strategy)
from .vault import TokenVault


class MaskResult:
    """mask 的结构化输出。

    - row：脱敏后的行（可直接落盘的安全数据）；
    - tokens：{字段: 还原令牌}，仅 MASK 字段有令牌，需单独保管；
    - audit：本次 mask 的审计事件；
    - missed：未在血缘策略中显式配置、走了默认策略兜底的字段。
    """

    def __init__(self, row, tokens, audit, missed):
        self.row = row
        self.tokens = tokens
        self.audit = audit
        self.missed = missed

    def __iter__(self):
        # 支持 row, tokens, audit, missed = mask(...) 解包
        return iter((self.row, self.tokens, self.audit, self.missed))

    def __eq__(self, other):
        if not isinstance(other, MaskResult):
            return NotImplemented
        return (self.row == other.row and self.tokens == other.tokens
                and self.missed == other.missed)

    def __repr__(self):
        return ("MaskResult(row=%r, tokens=%r, missed=%r)"
                % (self.row, self.tokens, self.missed))


class RevealResult:
    """reveal 的结构化输出：还原后的行 + 审计事件 + 实际还原的字段。"""

    def __init__(self, row, audit, restored_fields):
        self.row = row
        self.audit = audit
        self.restored_fields = restored_fields

    def __iter__(self):
        return iter((self.row, self.audit, self.restored_fields))

    def __repr__(self):
        return ("RevealResult(row=%r, restored_fields=%r)"
                % (self.row, self.restored_fields))


class RevealPermission:
    """节点级还原授权表。

    grant(operator, node) 授权；node="*" 为全节点通配。授权只针对
    reveal，mask 不读这张表。
    """

    WILDCARD = "*"

    def __init__(self):
        # operator -> set(node)
        self._grants = {}

    def grant(self, operator, node=WILDCARD):
        if not operator:
            raise PermissionDeniedError("anonymous operator cannot be granted")
        self._grants.setdefault(operator, set()).add(node)

    def revoke(self, operator, node=WILDCARD):
        """撤销授权（与令牌吊销是两回事：这里撤的是人的权限）。"""
        nodes = self._grants.get(operator)
        if nodes:
            nodes.discard(node)

    def can_reveal(self, operator, node):
        if not operator:
            return False
        nodes = self._grants.get(operator)
        if not nodes:
            return False
        return self.WILDCARD in nodes or node in nodes


class DesensEngine:
    """绑定一个血缘节点的脱敏/还原引擎。"""

    def __init__(self, lineage, node, default_strategy=DEFAULT_STRATEGY,
                 keyring=None, vault=None, permission=None, audit=None):
        validate_strategy(default_strategy)
        self.lineage = lineage
        self.node = node
        self.default_strategy = default_strategy
        self.keyring = keyring or Keyring()
        if not self.keyring.current_version:
            self.keyring.rotate()
        self.vault = vault or TokenVault(self.keyring)
        self.permission = permission or RevealPermission()
        self.audit = audit or AuditLog()
        # 节点生效规则，含可选的节点级默认（None 键）
        self._rules = lineage.effective_rules(node)
        self._node_default = self._rules.pop(NODE_DEFAULT, None)

    # ---- 策略查询 -------------------------------------------------------

    def effective_rules(self):
        """返回当前节点每个字段的生效策略（不含兜底默认）。"""
        return dict(self._rules)

    def strategy_for(self, field):
        """字段最终采用的策略：字段规则 > 节点默认 > 全局默认。"""
        return self._rules.get(field) or self._node_default \
            or self.default_strategy

    # ---- 脱敏 -----------------------------------------------------------

    def mask(self, row, operator=None):
        """对整行脱敏，返回 MaskResult。"""
        out = dict(row)
        tokens = {}
        missed = []
        applied = {}
        for field, value in row.items():
            explicit = field in self._rules
            strategy = self.strategy_for(field)
            applied[field] = strategy
            if not explicit:
                # 节点默认算节点显式配置的一部分，只有两层默认都没覆盖
                # 才登记为遗漏字段
                if self._node_default is None:
                    missed.append(field)
            out[field] = self._apply(strategy, field, value, tokens)
        audit_event = self.audit.record(
            "mask", operator, self.node,
            strategies=applied, missed=list(missed),
            key_version=self.keyring.current_version)
        return MaskResult(out, tokens, audit_event, missed)

    def _apply(self, strategy, field, value, tokens):
        if strategy == NONE:
            return value
        if strategy == REDACT:
            # 原值不进令牌库，任何手段都无法还原
            return None
        if strategy == HASH:
            return self._pseudonym(field, value)
        if strategy == MASK:
            token = self.vault.issue(self.node, field, value)
            tokens[field] = token
            return _mask_value(value)
        raise TokenError("unsupported strategy %r" % strategy)

    def _pseudonym(self, field, value):
        """HMAC-SHA256 截断假名：同值同假名，跨批次可关联、不可逆。"""
        message = ("%s\x00%s" % (field, value)).encode("utf-8")
        digest = self.keyring.pseudonym(message)
        return "h_" + digest[:16]

    # ---- 受控还原 -------------------------------------------------------

    def reveal(self, row, token=None, operator=None):
        """受控还原，返回 RevealResult。

        token 支持三种形态：单个令牌字符串（还原单字段行）、{字段: 令牌}
        映射（还原多字段）、None/空（必然拒绝并审计）。
        """
        try:
            token_map = self._normalize_tokens(token)
        except TokenError:
            # 连令牌形态都不合法，同样按一次还原尝试留痕
            self.audit.record(
                "reveal_denied", operator, self.node, reason="token")
            raise
        if not token_map:
            # 无令牌即无还原依据，即使有权限也拒绝并留痕
            self.audit.record(
                "reveal_denied", operator, self.node,
                reason="token", fields=[])
            raise TokenError(
                "reveal requires a token; none was provided")
        if not self.permission.can_reveal(operator, self.node):
            self.audit.record(
                "reveal_denied", operator, self.node,
                reason="permission", fields=sorted(token_map))
            raise PermissionDeniedError(
                "operator %r lacks reveal permission on node %r"
                % (operator, self.node))

        out = dict(row)
        restored = []
        key_versions = []
        for field, field_token in token_map.items():
            try:
                payload = self.vault.parse(field_token)
                # 单令牌形态：字段名以令牌内登记的为准
                if field is None:
                    field = payload["f"]
                self._check_token_scope(payload, field)
                out[field] = self.vault.fetch(payload)
                restored.append(field)
                key_versions.append(payload["v"])
            except (TokenError, TokenRevokedError):
                # 令牌非法/吊销：先留审计再向上抛，拒绝即留痕
                self.audit.record(
                    "reveal_denied", operator, self.node,
                    reason="token", field=field)
                raise

        self.audit.record(
            "reveal", operator, self.node,
            fields=restored,
            key_versions=sorted(set(key_versions)))
        return RevealResult(out, self.audit.events[-1], restored)

    def _check_token_scope(self, payload, field):
        if payload.get("f") != field:
            raise TokenError(
                "token field %r does not match requested field %r"
                % (payload.get("f"), field))
        if payload.get("n") != self.node:
            raise TokenError(
                "token issued for node %r cannot be used at node %r"
                % (payload.get("n"), self.node))

    @staticmethod
    def _normalize_tokens(token):
        if token is None:
            return {}
        if isinstance(token, str):
            # 单令牌：字段名从令牌内部解析，调用方无需再传一次
            return {None: token}
        if isinstance(token, dict):
            return dict(token)
        raise TokenError("token must be a string or a {field: token} dict")

    # ---- 管理操作 -------------------------------------------------------

    def revoke_token(self, token, operator=None):
        """吊销还原令牌，吊销后任何 reveal 立即失败；动作本身写审计。"""
        token_id = self.vault.revoke(token)
        self.audit.record("revoke", operator, self.node, token_id=token_id)
        return token_id


def _mask_value(value):
    """通用掩码：字符串保留首3尾2，其余打码；非字符串转字符串后整体打码。"""
    text = value if isinstance(value, str) else str(value)
    if len(text) > 5:
        return text[:3] + "*" * (len(text) - 5) + text[-2:]
    return "*" * len(text)
