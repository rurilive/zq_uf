"""入仓字段处理主流程：按规则脱敏、受控还原、审计与清单。"""
from .audit import AuditLog
from .keys import KeyRing
from .manifest import Manifest
from .rules import RuleRegistry
from .vault import Vault, VaultError


class MaskCtl:
    def __init__(self, keyring=None, registry=None, audit=None, manifest=None):
        self.keyring = keyring or KeyRing()
        self.registry = registry or RuleRegistry()
        self.audit = audit or AuditLog()
        self.manifest = manifest or Manifest()
        self.vault = Vault(self.keyring)
        self._grants = set()  # (requester, table) 还原授权

    # ---- 还原授权 ----

    def grant_reveal(self, requester, table, granted_by):
        """授权某请求人还原某表，授权人必须是规则审批人。"""
        if granted_by not in self.registry.approvers:
            raise PermissionError(f"{granted_by} 无授权权限")
        self._grants.add((requester, table))
        self.audit.record(
            granted_by, "grant_reveal", table, detail={"requester": requester}
        )

    def revoke_reveal(self, requester, table, revoked_by):
        if revoked_by not in self.registry.approvers:
            raise PermissionError(f"{revoked_by} 无授权权限")
        self._grants.discard((requester, table))
        self.audit.record(
            revoked_by, "revoke_reveal", table, detail={"requester": requester}
        )

    # ---- 入仓脱敏 ----

    def mask(self, row, table, actor="ingest"):
        """按字段规则处理一行数据。

        返回 {"row", "masked", "token", "audit"}：
        - row：处理后的数据，可直接落库；
        - masked：被处理过的字段，下游可据此识别；
        - token：还原令牌（本行没有敏感字段时为 None）。
        """
        out = {}
        masked = []
        originals = {}
        for field, value in row.items():
            rule, is_plain, source = self.registry.rule_for(table, field)
            if is_plain:
                out[field] = value
                continue
            out[field] = rule.apply(value)
            masked.append(field)
            originals[field] = value
            self.manifest.note(
                table, field, source, self.registry.version,
                self.keyring.current_version,
            )
        token = self.vault.seal(table, originals) if originals else None
        return {"row": out, "masked": masked, "token": token, "audit": []}

    # ---- 受控还原 ----

    def reveal(self, token, requester, fields=None):
        """凭令牌还原原值。需事先授权；失败不返回任何原值，并写审计。"""
        try:
            table = self.vault.table_of(token)
        except VaultError as e:
            entry = self.audit.record(
                requester, "reveal", None, ok=False, detail={"reason": str(e)}
            )
            return {"row": {}, "revealed": False, "audit": [entry]}
        if (requester, table) not in self._grants:
            entry = self.audit.record(
                requester, "reveal", table, ok=False,
                detail={"reason": "unauthorized"},
            )
            return {"row": {}, "revealed": False, "audit": [entry]}
        try:
            values = self.vault.open(token, fields)
        except VaultError as e:
            entry = self.audit.record(
                requester, "reveal", table, ok=False, detail={"reason": str(e)}
            )
            return {"row": {}, "revealed": False, "audit": [entry]}
        entry = self.audit.record(
            requester, "reveal", table, detail={"fields": sorted(values)}
        )
        return {"row": values, "revealed": True, "audit": [entry]}
