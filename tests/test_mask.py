import unittest

from maskctl import (
    ALERTS,
    AUDIT_LOG,
    export_inventory,
    mask,
    reveal,
    rotate_key,
    set_rule,
)


class MaskTest(unittest.TestCase):
    def test_plaintext_passthrough(self):
        self.assertEqual(mask({"phone": "123"})["row"], {"phone": "123"})

    def test_no_masked_fields(self):
        self.assertEqual(mask({"phone": "123"})["masked"], [])

    def test_reveal_is_false(self):
        self.assertFalse(reveal({"phone": "***"}, "t")["revealed"])


class RuleMaskTest(unittest.TestCase):
    def test_phone_keeps_prefix_suffix(self):
        res = mask({"phone": "13800000000"})
        self.assertEqual(res["row"]["phone"], "138****0000")
        self.assertEqual(res["masked"], ["phone"])
        self.assertIsNotNone(res["token"])

    def test_table_rule_applies(self):
        res = mask({"address": "北京市海淀区中关村大街1号"}, table="users")
        self.assertEqual(res["row"]["address"], "北京市海淀区*******")

    def test_too_short_value_skipped(self):
        res = mask({"phone": "123"})
        self.assertEqual(res["row"]["phone"], "123")
        self.assertEqual(res["masked"], [])
        self.assertIsNone(res["token"])

    def test_non_string_skipped(self):
        res = mask({"age": 30})
        self.assertEqual(res["row"]["age"], 30)
        self.assertEqual(res["masked"], [])


class DefaultPolicyTest(unittest.TestCase):
    def test_unconfigured_field_uses_default_policy(self):
        res = mask({"nickname": "abcdef"})
        self.assertEqual(res["row"]["nickname"], "a*****")
        self.assertEqual(res["masked"], ["nickname"])

    def test_new_field_auto_inherits(self):
        res = mask({"brand_new_field": "sensitive-value"})
        self.assertEqual(res["row"]["brand_new_field"], "s**************")
        self.assertIn("brand_new_field", res["masked"])

    def test_table_falls_back_to_default_table_rules(self):
        res = mask({"phone": "13800000000"}, table="users")
        self.assertEqual(res["row"]["phone"], "138****0000")


class ControlledRevealTest(unittest.TestCase):
    def test_reveal_with_valid_token(self):
        row = {"phone": "13800000000", "name": "张三"}
        res = mask(row, requester="collector-01")
        back = reveal(res["row"], res["token"], requester="dev-li")
        self.assertTrue(back["revealed"])
        self.assertEqual(back["row"]["phone"], "13800000000")
        self.assertEqual(back["row"]["name"], "张三")

    def test_reveal_invalid_token_alerts_and_records_requester(self):
        before = len(ALERTS)
        back = reveal({"phone": "138****0000"}, "bad-token", requester="dev-li")
        self.assertFalse(back["revealed"])
        self.assertEqual(len(ALERTS), before + 1)
        self.assertEqual(ALERTS[-1]["requester"], "dev-li")
        self.assertEqual(ALERTS[-1]["reason"], "reveal_denied")

    def test_reveal_without_permission_denied(self):
        res = mask({"phone": "13800000000"}, role="writer")
        before = len(ALERTS)
        back = reveal(res["row"], res["token"], requester="dev-li")
        self.assertFalse(back["revealed"])
        self.assertEqual(len(ALERTS), before + 1)

    def test_old_token_survives_key_rotation(self):
        res = mask({"phone": "13800000000"})
        rotate_key("v2", "maskctl-dev-key-v2", approver="admin")
        try:
            back = reveal(res["row"], res["token"])
            self.assertTrue(back["revealed"])
            self.assertEqual(back["row"]["phone"], "13800000000")
        finally:
            rotate_key("v1", "maskctl-dev-key-v1", approver="admin")


class AuditTest(unittest.TestCase):
    def test_mask_writes_audit(self):
        before = len(AUDIT_LOG)
        res = mask({"phone": "13800000000"}, requester="collector-01")
        self.assertEqual(len(AUDIT_LOG), before + 1)
        self.assertEqual(res["audit"][0]["op"], "mask")
        self.assertEqual(res["audit"][0]["result"], "masked")
        self.assertEqual(res["audit"][0]["requester"], "collector-01")

    def test_reveal_writes_audit(self):
        res = mask({"phone": "13800000000"})
        back = reveal(res["row"], res["token"], requester="dev-li")
        self.assertEqual(back["audit"][-1]["op"], "reveal")
        self.assertEqual(back["audit"][-1]["result"], "ok")
        self.assertEqual(back["audit"][-1]["requester"], "dev-li")

    def test_failed_reveal_audited(self):
        back = reveal({"phone": "***"}, "bad-token", requester="dev-li")
        self.assertEqual(back["audit"][-1]["result"], "denied:invalid_token")


class RuleChangeApprovalTest(unittest.TestCase):
    def test_rule_change_requires_approval(self):
        with self.assertRaises(PermissionError):
            set_rule("users", "id_card", {"prefix": 4, "suffix": 4},
                     approver="dev-li")

    def test_approved_rule_change_applies(self):
        set_rule("users", "id_card", {"prefix": 4, "suffix": 4},
                 approver="admin")
        res = mask({"id_card": "110101199001011234"}, table="users")
        self.assertEqual(res["row"]["id_card"], "1101**********1234")

    def test_rule_change_audited(self):
        before = len(AUDIT_LOG)
        set_rule("users", "email", {"prefix": 1, "suffix": 0}, approver="admin")
        self.assertEqual(AUDIT_LOG[-1]["op"], "rule_change")
        self.assertEqual(AUDIT_LOG[-1]["result"], "approved")
        self.assertGreater(len(AUDIT_LOG), before)


class InventoryTest(unittest.TestCase):
    def test_inventory_grouped_by_table(self):
        inv = export_inventory()
        self.assertIn("users", inv)
        self.assertIn("default", inv)
        self.assertIn("phone", inv["users"])
        self.assertIn("<default_policy>", inv["*"])
