import unittest

from formfill import (
    DialogBlocked,
    KeyCoordinator,
    ReceiptStore,
    submit_batch,
)
from formfill.template import Template


def rows():
    return [
        {"id": 1, "date": "2026-03-01", "amount": "100"},
        {"id": 2, "date": "2026-03-01", "amount": "200"},
        {"id": 3, "date": "2026-03-01", "amount": "300"},
        {"id": 4, "date": "2026-03-01", "amount": "bad"},
    ]


class DialogTest(unittest.TestCase):
    def setUp(self):
        self.store = ReceiptStore()
        self.coordinator = KeyCoordinator()

    def test_confirm_dialog_accepted_then_submit_succeeds(self):
        seen = {"confirmed": False}

        def submit_one(row):
            if not seen["confirmed"]:
                seen["confirmed"] = True
                raise DialogBlocked("confirm", "请确认报销金额")
            self.assertEqual(row["_dialog_confirmed"], "confirm")
            return {"status": "ok", "receipt_id": "R-C"}

        result = submit_batch(
            [rows()[0]], workers=1, submit_one=submit_one,
            store=self.store, coordinator=self.coordinator,
        )
        self.assertEqual(len(result.success), 1)
        self.assertTrue(seen["confirmed"])
        self.assertEqual(self.store.get("1").dialog, "confirm")

    def test_validation_dialog_dismissed_with_fields_in_report(self):
        def submit_one(row):
            return {
                "status": "dialog",
                "dialog": {
                    "type": "validation",
                    "message": "金额超出额度",
                    "fields": [{"field": "amount", "reason": "超限"}],
                },
            }

        result = submit_batch(
            [rows()[0]], workers=1, submit_one=submit_one,
            store=self.store, coordinator=self.coordinator,
        )
        self.assertEqual(len(result.failed), 1)
        failed = result.failed[0]
        self.assertEqual(failed.dialog, "validation")
        self.assertEqual(failed.fields[0]["field"], "amount")
        self.assertEqual(failed.fields[0]["value"], "100")

    def test_unknown_dialog_goes_manual(self):
        def submit_one(row):
            return {"dialog": {"type": "captcha", "message": "请输入验证码"}}

        result = submit_batch(
            [rows()[0]], workers=1, submit_one=submit_one,
            store=self.store, coordinator=self.coordinator,
        )
        self.assertEqual(len(result.manual), 1)
        self.assertEqual(result.manual[0].dialog, "captcha")

    def test_single_failure_does_not_stop_the_batch(self):
        def submit_one(row):
            if row["key"] == "2":
                raise RuntimeError("单据服务抖动")
            return {"status": "ok"}

        result = submit_batch(
            rows()[:3], workers=3, submit_one=submit_one,
            store=self.store, coordinator=self.coordinator,
        )
        self.assertEqual(len(result.success), 2)
        self.assertEqual(len(result.failed), 1)


class FourListsTest(unittest.TestCase):
    def test_report_partitions_and_field_errors(self):
        store, coordinator = ReceiptStore(), KeyCoordinator()

        def submit_one(row):
            if row["key"] == "2":
                return {"status": "rejected", "message": "发票号缺失"}
            if row["key"] == "3":
                raise TimeoutError("提交超时")
            return {"status": "ok", "receipt_id": f"R-{row['key']}"}

        result = submit_batch(
            rows(), workers=4, submit_one=submit_one,
            store=store, coordinator=coordinator,
        )
        self.assertEqual(result.counts(),
                         {"success": 1, "skipped": 0, "failed": 2, "manual": 1, "preview": 0})
        self.assertEqual([item.business_key for item in result], ["1", "2", "3", "4"])
        report = result.report()
        self.assertEqual(report["counts"]["failed"], 2)
        bad_amount = next(item for item in report["failed"] if item["business_key"] == "4")
        self.assertEqual(bad_amount["fields"][0]["field"], "amount")
        self.assertEqual(bad_amount["fields"][0]["value"], "bad")


class DryRunTest(unittest.TestCase):
    def test_dry_run_previews_without_submit_or_receipt_write(self):
        store, coordinator = ReceiptStore(), KeyCoordinator()
        calls = []
        result = submit_batch(
            rows(), workers=4,
            submit_one=lambda row: calls.append(row["key"]) or {"status": "ok"},
            store=store, coordinator=coordinator, dry_run=True,
        )
        self.assertTrue(result.dry_run)
        self.assertEqual(calls, [])
        self.assertEqual(len(result.preview), 3)
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(store.all(), [])

    def test_dry_run_marks_existing_success_as_skipped(self):
        from formfill import Receipt

        store, coordinator = ReceiptStore(), KeyCoordinator()
        store.save(Receipt("1", "ok", receipt_id="R-1"))
        result = submit_batch(
            [rows()[0]], workers=1, submit_one=lambda row: {"status": "ok"},
            store=store, coordinator=coordinator, dry_run=True,
        )
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(len(result.preview), 0)

    def test_dry_run_uses_template_columns(self):
        template = Template(
            date_input_formats=("%Y/%m/%d",),
            field_map={"date": "d", "amount": "amt"},
        )
        data = [{"id": 1, "d": "2026/03/01", "amt": "1,234.5"}]
        result = submit_batch(
            data, workers=1, submit_one=lambda row: {"status": "ok"},
            template=template, store=ReceiptStore(), coordinator=KeyCoordinator(),
            dry_run=True,
        )
        self.assertEqual([item.business_key for item in result.preview], ["1"])


if __name__ == "__main__":
    unittest.main()
