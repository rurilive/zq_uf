import unittest

from logmask import (
    FULL,
    PARTIAL,
    PLAIN,
    UnregisteredFieldError,
    configure,
    mask,
    report,
    reset,
    rollback,
    versions,
)

ORDER_RULES = {
    "phone": {"mode": PARTIAL, "keep_head": 3, "keep_tail": 4},
    "id_card": FULL,
    "card_no": FULL,
    "order_no": PLAIN,
    "note": PLAIN,
}

RECORD = {
    "service": "order",
    "phone": "13800001111",
    "id_card": "110101199003071234",
    "order_no": "2026010112345678",
    "note": "user called back",
}


class MaskTest(unittest.TestCase):
    def setUp(self):
        reset()
        configure("order", ORDER_RULES)
        configure("pay", {"phone": FULL, "card_no": FULL})

    # ---- 按字段和用途区分处理方式 ----

    def test_partial_keeps_head_and_tail(self):
        result = mask(RECORD)
        self.assertEqual(result["record"]["phone"], "138****1111")
        self.assertEqual(result["record"]["id_card"], "***")

    def test_plain_field_is_preserved_for_business_use(self):
        result = mask(RECORD)
        self.assertEqual(result["record"]["order_no"], "2026010112345678")
        self.assertEqual(result["record"]["note"], "user called back")

    def test_masked_list_reports_field_mode_and_value(self):
        result = mask(RECORD)
        modes = {item["field"]: item["mode"] for item in result["masked"]}
        self.assertEqual(modes["phone"], PARTIAL)
        self.assertEqual(modes["id_card"], FULL)
        self.assertNotIn("order_no", modes)
        self.assertNotIn("note", modes)

    def test_partial_falls_back_to_full_for_short_values(self):
        configure("order", {"phone": {"mode": PARTIAL, "keep_head": 3,
                                      "keep_tail": 4}})
        result = mask({"service": "order", "phone": "1234"})
        self.assertEqual(result["record"]["phone"], "***")

    def test_non_string_scalar_is_masked_as_text(self):
        configure("pay", {"amount": PLAIN, "phone": PARTIAL})
        result = mask({"service": "pay", "amount": 100, "phone": 13800001111})
        self.assertEqual(result["record"]["amount"], 100)
        self.assertEqual(result["record"]["phone"], "138****1111")

    def test_input_record_is_not_mutated(self):
        original = dict(RECORD)
        mask(RECORD)
        self.assertEqual(RECORD, original)

    # ---- 未登记字段 fail-closed ----

    def test_unregistered_field_is_rejected_with_its_name(self):
        bad = dict(RECORD, device="13900002222")
        with self.assertRaises(UnregisteredFieldError) as ctx:
            mask(bad)
        self.assertEqual(ctx.exception.service, "order")
        self.assertEqual(ctx.exception.fields, ["device"])
        self.assertIn("device", str(ctx.exception))

    def test_unknown_service_rejects_every_field(self):
        with self.assertRaises(UnregisteredFieldError) as ctx:
            mask({"service": "new-svc", "phone": "13800001111"})
        self.assertEqual(ctx.exception.fields, ["phone"])

    def test_rejected_record_is_not_masked_or_written(self):
        bad = dict(RECORD, secret="x")
        with self.assertRaises(UnregisteredFieldError):
            mask(bad)
        # 入参保持明文由调用方负责，库不产出脱敏副本也不产生一条成功日志
        body = report()
        self.assertEqual(body["services"]["order"]["calls"], 0)

    # ---- 按服务分别配置，同字段不同处理 ----

    def test_same_field_uses_different_mode_per_service(self):
        order_result = mask({"service": "order", "phone": "13800001111"})
        pay_result = mask({"service": "pay", "phone": "13800001111"})
        self.assertEqual(order_result["record"]["phone"], "138****1111")
        self.assertEqual(pay_result["record"]["phone"], "***")

    def test_field_registered_in_one_service_is_unknown_in_another(self):
        with self.assertRaises(UnregisteredFieldError):
            mask({"service": "pay", "id_card": "110101199003071234"})

    # ---- 关联标识稳定 ----

    def test_trace_id_is_returned_and_stable_for_same_record(self):
        first = mask(RECORD)
        second = mask(RECORD)
        self.assertTrue(first["trace_id"].startswith("tid_"))
        self.assertEqual(first["trace_id"], second["trace_id"])

    def test_trace_id_stays_stable_across_rule_version_changes(self):
        before = mask(RECORD)["trace_id"]
        configure("order", {**ORDER_RULES, "phone": FULL},
                  note="harden phone")
        after = mask(RECORD)["trace_id"]
        self.assertEqual(before, after)

    def test_trace_id_differs_for_different_records(self):
        a = mask(RECORD)["trace_id"]
        b = mask(dict(RECORD, phone="13800009999"))["trace_id"]
        self.assertNotEqual(a, b)

    # ---- 规则版本：只对新日志生效、可回滚 ----

    def test_rule_change_creates_a_version_only_affecting_new_logs(self):
        v1 = mask(RECORD)["version"]
        new_version = configure("order", {**ORDER_RULES, "phone": FULL})
        self.assertEqual(new_version, v1 + 1)
        result = mask(RECORD)
        self.assertEqual(result["version"], new_version)
        self.assertEqual(result["record"]["phone"], "***")
        self.assertEqual([v["version"] for v in versions("order")], [1, 2])

    def test_rollback_restores_previous_rules_as_a_new_version(self):
        configure("order", {**ORDER_RULES, "phone": FULL})
        self.assertEqual(mask(RECORD)["record"]["phone"], "***")
        restored = rollback("order")
        self.assertEqual(restored, 3)
        result = mask(RECORD)
        self.assertEqual(result["version"], 3)
        self.assertEqual(result["record"]["phone"], "138****1111")
        notes = [v["note"] for v in versions("order")]
        self.assertEqual(notes[-1], "rollback to v1")

    def test_rollback_to_specific_version_and_unknown_version_guard(self):
        configure("order", {**ORDER_RULES, "phone": FULL})
        configure("order", ORDER_RULES, note="restore-like")
        new_version = rollback("order", version=1)
        self.assertEqual(new_version, 4)
        with self.assertRaises(ValueError):
            rollback("order", version=99)
        with self.assertRaises(ValueError):
            rollback("never-configured")

    def test_rollback_is_scoped_per_service(self):
        configure("risk", {"phone": PARTIAL})
        configure("risk", {"phone": FULL})
        rollback("risk")
        risk_result = mask({"service": "risk", "phone": "13800001111"})
        pay_result = mask({"service": "pay", "phone": "13800001111"})
        self.assertEqual(risk_result["record"]["phone"], "138****1111")
        self.assertEqual(pay_result["record"]["phone"], "***")

    # ---- 统计 ----

    def test_report_aggregates_per_service_with_breakdown(self):
        mask(RECORD)
        for _ in range(2):
            with self.assertRaises(UnregisteredFieldError):
                mask(dict(RECORD, device="x"))
        with self.assertRaises(UnregisteredFieldError):
            mask(dict(RECORD, fingerprint="y", device="z"))

        body = report()
        order = body["services"]["order"]
        self.assertEqual(order["calls"], 1)
        self.assertEqual(order["masked_fields"], 2)  # phone + id_card
        self.assertEqual(order["unregistered_fields"], 4)
        self.assertEqual(order["unregistered_breakdown"],
                         {"device": 3, "fingerprint": 1})
        self.assertEqual(body["unregistered"], 4)
        self.assertGreaterEqual(body["trace_ids"], 1)

    def test_report_tracks_rejections_from_unknown_services(self):
        with self.assertRaises(UnregisteredFieldError):
            mask({"service": "ghost", "leak": "v"})
        body = report()
        self.assertIn("ghost", body["services"])
        self.assertEqual(
            body["services"]["ghost"]["unregistered_breakdown"],
            {"leak": 1},
        )

    def test_configure_rejects_invalid_mode(self):
        with self.assertRaises(ValueError):
            configure("bad", {"phone": "erase"})


if __name__ == "__main__":
    unittest.main()
