"""受控还原：令牌 + 权限 + 审计，三者缺一不可。"""

import unittest

from tabledesens import (DesensEngine, Lineage, MASK, HASH,
                         PermissionDeniedError, TokenError,
                         TokenRevokedError)


def make_engine(audit=None):
    lineage = Lineage(
        edges=[("ods.user", "dwd.user")],
        rules={"ods.user": {"phone": MASK, "id_card": MASK,
                            "city": HASH}})
    return DesensEngine(lineage, node="dwd.user", audit=audit)


class ControlledRevealTest(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()
        self.row = {"phone": "13800000000", "id_card": "310101199001011234",
                    "city": "shanghai"}
        result = self.engine.mask(self.row, operator="etl-bot")
        self.masked = result.row
        self.tokens = result.tokens

    def test_reveal_with_token_and_permission(self):
        self.engine.permission.grant("analyst", "dwd.user")
        result = self.engine.reveal(self.masked, self.tokens,
                                    operator="analyst")
        self.assertEqual(result.row["phone"], "13800000000")
        self.assertEqual(result.row["id_card"], "310101199001011234")
        self.assertEqual(set(result.restored_fields), {"phone", "id_card"})
        # HASH 字段没有令牌，脱敏值原样保留
        self.assertTrue(self.masked["city"].startswith("h_"))

    def test_reveal_denied_without_permission(self):
        with self.assertRaises(PermissionDeniedError):
            self.engine.reveal(self.masked, self.tokens, operator="guest")
        # 拒绝也必须留痕
        denied = self.engine.audit.query(action="reveal_denied")
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["reason"], "permission")

    def test_anonymous_reveal_denied(self):
        with self.assertRaises(PermissionDeniedError):
            self.engine.reveal(self.masked, self.tokens, operator=None)

    def test_reveal_without_token_denied(self):
        self.engine.permission.grant("analyst", "dwd.user")
        with self.assertRaises(TokenError):
            self.engine.reveal(self.masked, None, operator="analyst")

    def test_bad_token_type_denied_and_audited(self):
        self.engine.permission.grant("analyst", "dwd.user")
        with self.assertRaises(TokenError):
            self.engine.reveal(self.masked, 12345, operator="analyst")
        denied = self.engine.audit.query(action="reveal_denied")
        self.assertEqual(denied[-1]["reason"], "token")

    def test_forged_token_rejected(self):
        self.engine.permission.grant("analyst", "dwd.user")
        forged = self.tokens["phone"][:-2] + "aa"
        with self.assertRaises(TokenError):
            self.engine.reveal(self.masked,
                               {"phone": forged}, operator="analyst")
        denied = self.engine.audit.query(action="reveal_denied")
        self.assertEqual(denied[0]["reason"], "token")

    def test_single_token_string_form(self):
        self.engine.permission.grant("analyst", "dwd.user")
        token = self.tokens["phone"]
        result = self.engine.reveal({"phone": self.masked["phone"]},
                                    token, operator="analyst")
        self.assertEqual(result.row["phone"], "13800000000")

    def test_token_bound_to_node(self):
        # 另一个节点的引擎拿到同一令牌也不能用
        other_lineage = Lineage(rules={"other.node": {"phone": MASK}})
        other = DesensEngine(other_lineage, node="other.node",
                             vault=self.engine.vault,
                             permission=self.engine.permission)
        other.permission.grant("analyst", "other.node")
        with self.assertRaises(TokenError):
            other.reveal({"phone": self.masked["phone"]},
                         self.tokens["phone"], operator="analyst")

    def test_wildcard_permission(self):
        self.engine.permission.grant("admin")  # 默认 "*" 通配
        result = self.engine.reveal(self.masked, self.tokens,
                                    operator="admin")
        self.assertEqual(result.row["phone"], "13800000000")

    def test_permission_revoke_blocks_reveal(self):
        self.engine.permission.grant("analyst", "dwd.user")
        self.engine.permission.revoke("analyst", "dwd.user")
        with self.assertRaises(PermissionDeniedError):
            self.engine.reveal(self.masked, self.tokens, operator="analyst")


class TokenRevocationTest(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()
        result = self.engine.mask({"phone": "13800000000"}, operator="etl")
        self.token = result.tokens["phone"]
        self.masked = result.row
        self.engine.permission.grant("analyst", "dwd.user")

    def test_revoked_token_fails_immediately(self):
        self.engine.reveal(self.masked, self.token, operator="analyst")
        self.engine.revoke_token(self.token, operator="secops")
        with self.assertRaises(TokenRevokedError):
            self.engine.reveal(self.masked, self.token, operator="analyst")

    def test_revoke_is_idempotent(self):
        first = self.engine.revoke_token(self.token)
        second = self.engine.revoke_token(self.token)
        self.assertEqual(first, second)

    def test_revoke_writes_audit(self):
        self.engine.revoke_token(self.token, operator="secops")
        events = self.engine.audit.query(action="revoke")
        self.assertEqual(events[0]["operator"], "secops")

    def test_re_mask_rotates_token_and_revokes_old_one(self):
        # 重新脱敏产生新令牌，旧令牌自动吊销、立即失效
        new_result = self.engine.mask({"phone": "13800000000"},
                                      operator="etl")
        self.assertNotEqual(new_result.tokens["phone"], self.token)
        self.assertTrue(self.engine.vault.is_revoked(self.token))
        with self.assertRaises(TokenRevokedError):
            self.engine.reveal(self.masked, self.token, operator="analyst")
        # 新令牌不受影响
        restored = self.engine.reveal(
            new_result.row, new_result.tokens["phone"], operator="analyst")
        self.assertEqual(restored.row["phone"], "13800000000")

    def test_third_issue_keeps_only_latest_token_valid(self):
        first = self.engine.mask({"phone": "13800000000"},
                                 operator="etl").tokens["phone"]
        second = self.engine.mask({"phone": "13800000000"},
                                  operator="etl").tokens["phone"]
        third = self.engine.mask({"phone": "13900000000"},
                                 operator="etl").tokens["phone"]
        self.assertTrue(self.engine.vault.is_revoked(first))
        self.assertTrue(self.engine.vault.is_revoked(second))
        self.assertFalse(self.engine.vault.is_revoked(third))

    def test_is_revoked_helper(self):
        self.assertFalse(self.engine.vault.is_revoked(self.token))
        self.engine.revoke_token(self.token)
        self.assertTrue(self.engine.vault.is_revoked(self.token))


class KeyVersionTest(unittest.TestCase):
    def test_rotation_creates_new_version(self):
        engine = make_engine()
        self.assertEqual(engine.keyring.current_version, 1)
        engine.keyring.rotate()
        self.assertEqual(engine.keyring.current_version, 2)
        result = engine.mask({"phone": "13800000000"}, operator="etl")
        # 新令牌用新版本密钥，但旧版本密钥仍保留可验旧令牌
        self.assertEqual(result.audit["key_version"], 2)

    def test_token_survives_key_rotation(self):
        engine = make_engine()
        engine.permission.grant("analyst", "dwd.user")
        result = engine.mask({"phone": "13800000000"}, operator="etl")
        token = result.tokens["phone"]
        engine.keyring.rotate()  # 轮换后旧令牌仍可还原
        restored = engine.reveal(result.row, token, operator="analyst")
        self.assertEqual(restored.row["phone"], "13800000000")

    def test_token_records_key_version(self):
        engine = make_engine()
        result = engine.mask({"phone": "13800000000"}, operator="etl")
        payload = engine.vault.parse(result.tokens["phone"])
        self.assertEqual(payload["v"], engine.keyring.current_version)

    def test_reveal_audit_carries_token_key_version(self):
        engine = make_engine()
        engine.permission.grant("analyst", "dwd.user")
        result = engine.mask({"phone": "13800000000"}, operator="etl")
        old_version = engine.keyring.current_version
        engine.keyring.rotate()
        reveal = engine.reveal(result.row, result.tokens["phone"],
                               operator="analyst")
        # 审计记录的是令牌实际使用的旧版本，而不是密钥环当前版本
        self.assertEqual(reveal.audit["key_versions"], [old_version])
        self.assertNotIn(old_version, [engine.keyring.current_version])


if __name__ == "__main__":
    unittest.main()
