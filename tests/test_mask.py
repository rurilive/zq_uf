import unittest

from maskctl import MaskCtl, Rule, RuleRegistry


def make_ctl():
    registry = RuleRegistry(approvers={"lead"}, oncall={"oncall"})
    ctl = MaskCtl(registry=registry)
    cid = registry.submit_change(
        "dev", "users", "phone", rule=Rule(keep_prefix=3, keep_suffix=4)
    )
    registry.approve(cid, "lead")
    return ctl, registry


class MaskTest(unittest.TestCase):
    def test_rule_keeps_prefix_suffix(self):
        ctl, _ = make_ctl()
        r = ctl.mask({"phone": "13800000000"}, table="users")
        self.assertEqual(r["row"]["phone"], "138****0000")
        self.assertEqual(r["masked"], ["phone"])

    def test_default_policy_covers_unconfigured_field(self):
        ctl, _ = make_ctl()
        r = ctl.mask({"id_card": "110101199001011234"}, table="users")
        self.assertEqual(r["row"]["id_card"], "*" * 18)
        self.assertEqual(r["masked"], ["id_card"])

    def test_plain_field_passthrough(self):
        ctl, registry = make_ctl()
        cid = registry.submit_change("dev", "users", "name", plain=True)
        registry.approve(cid, "lead")
        r = ctl.mask({"name": "张三"}, table="users")
        self.assertEqual(r["row"], {"name": "张三"})
        self.assertEqual(r["masked"], [])
        self.assertIsNone(r["token"])

    def test_reveal_requires_grant(self):
        ctl, _ = make_ctl()
        r = ctl.mask({"phone": "13800000000"}, table="users")
        denied = ctl.reveal(r["token"], requester="dev")
        self.assertFalse(denied["revealed"])
        self.assertEqual(denied["row"], {})  # 失败不返回原值

    def test_reveal_with_grant_returns_original(self):
        ctl, _ = make_ctl()
        r = ctl.mask({"phone": "13800000000"}, table="users")
        ctl.grant_reveal("dev", "users", granted_by="lead")
        ok = ctl.reveal(r["token"], requester="dev")
        self.assertTrue(ok["revealed"])
        self.assertEqual(ok["row"], {"phone": "13800000000"})

    def test_reveal_bad_token_returns_nothing(self):
        ctl, _ = make_ctl()
        ctl.grant_reveal("dev", "users", granted_by="lead")
        bad = ctl.reveal("v1.tok-999", requester="dev")
        self.assertFalse(bad["revealed"])
        self.assertEqual(bad["row"], {})

    def test_reveal_audit_records_requester_and_time(self):
        ctl, _ = make_ctl()
        r = ctl.mask({"phone": "13800000000"}, table="users")
        ctl.reveal(r["token"], requester="dev")
        entry = ctl.audit.entries[-1]
        self.assertEqual(entry["actor"], "dev")
        self.assertEqual(entry["action"], "reveal")
        self.assertFalse(entry["ok"])
        self.assertTrue(entry["time"])

    def test_key_rotation_keeps_old_tokens_readable(self):
        ctl, _ = make_ctl()
        r = ctl.mask({"phone": "13800000000"}, table="users")
        ctl.keyring.add_key()  # 轮换密钥
        ctl.grant_reveal("dev", "users", granted_by="lead")
        self.assertEqual(
            ctl.reveal(r["token"], requester="dev")["row"],
            {"phone": "13800000000"},
        )

    def test_rule_change_needs_approval(self):
        ctl, registry = make_ctl()
        cid = registry.submit_change(
            "dev", "users", "phone", rule=Rule(keep_prefix=0, keep_suffix=0)
        )
        # 未审批不生效
        r = ctl.mask({"phone": "13800000000"}, table="users")
        self.assertEqual(r["row"]["phone"], "138****0000")
        with self.assertRaises(PermissionError):
            registry.approve(cid, "dev")  # 非审批人
        registry.approve(cid, "lead")
        r = ctl.mask({"phone": "13800000000"}, table="users")
        self.assertEqual(r["row"]["phone"], "*" * 11)

    def test_rule_change_does_not_reprocess_history(self):
        ctl, registry = make_ctl()
        old = ctl.mask({"phone": "13800000000"}, table="users")
        cid = registry.submit_change(
            "dev", "users", "phone", rule=Rule(keep_prefix=0, keep_suffix=0)
        )
        registry.approve(cid, "lead")
        ctl.grant_reveal("dev", "users", granted_by="lead")
        # 历史令牌仍还原原值，历史落库数据不重新处理
        self.assertEqual(
            ctl.reveal(old["token"], requester="dev")["row"],
            {"phone": "13800000000"},
        )

    def test_approval_records_summary_and_view_perm(self):
        _, registry = make_ctl()
        summary = registry.approval_summary(viewer="oncall")
        self.assertEqual(summary, {"users": {"approved": 1, "rejected": 0}})
        records = registry.approvals(table="users", viewer="oncall")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["proposer"], "dev")
        with self.assertRaises(PermissionError):
            registry.approval_summary(viewer="stranger")

    def test_approvals_export_csv(self):
        _, registry = make_ctl()
        text = registry.export_approvals(table="users", viewer="oncall")
        self.assertIn("change_id,table,field", text)
        self.assertIn("users,phone,dev,lead,approved", text)

    def test_manifest_export_by_table(self):
        ctl, _ = make_ctl()
        ctl.mask({"phone": "13800000000", "id_card": "110101199001011234"},
                 table="users")
        text = ctl.manifest.export("users")
        self.assertIn("users,phone,rule", text)
        self.assertIn("users,id_card,default", text)


if __name__ == "__main__":
    unittest.main()
