from datetime import datetime, timezone
from decimal import Decimal
import unittest

from formfill import FieldFormatError, Template, normalize_row


class TemplateFormatTest(unittest.TestCase):
    def test_default_template_matches_legacy_behavior(self):
        row = normalize_row({"id": 1, "date": "2026-3-1", "amount": "12.345"})
        self.assertEqual(row["date"], "2026-03-01")
        self.assertEqual(row["amount"], 12.35)
        self.assertEqual(row["key"], "1")

    def test_composite_business_key(self):
        template = Template(key_fields=("company", "month", "id"))
        row = {"company": "acme", "month": "2026-03", "id": 5,
               "date": "2026-03-01", "amount": "1"}
        self.assertEqual(template.business_key(row), "acme:2026-03:5")
        self.assertEqual(template.normalize(row)["key"], "acme:2026-03:5")

    def test_missing_key_field_reports_original_value(self):
        template = Template(key_fields=("id", "seq"))
        with self.assertRaises(FieldFormatError) as ctx:
            template.business_key({"id": 3, "date": "2026-03-01", "amount": "1"})
        self.assertEqual(ctx.exception.field_name, "seq")
        self.assertEqual(ctx.exception.value, None)

    def test_date_formats_by_template(self):
        cn = Template(date_input_formats=("%Y/%m/%d", "%d-%m-%Y"),
                      date_output_format="%Y%m%d")
        self.assertEqual(
            cn.convert_date("2026/03/07"), "20260307"
        )
        self.assertEqual(
            cn.convert_date("07-03-2026"), "20260307"
        )

    def test_timezone_applied_to_timestamp_and_naive_datetime(self):
        hkt = Template(timezone="Asia/Hong_Kong")
        utc = Template(timezone="UTC")
        moment = datetime(2026, 3, 1, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(hkt.convert_date(moment), "2026-03-02")
        self.assertEqual(utc.convert_date(moment), "2026-03-01")
        naive = datetime(2026, 3, 1, 23, 30)
        self.assertEqual(hkt.convert_date(naive), "2026-03-01")

    def test_amount_precision_and_rounding_configurable(self):
        precision4 = Template(amount_precision=4, amount_rounding="half_even")
        self.assertEqual(precision4.convert_amount("12.34567"), 12.3457)
        half_up = Template(amount_rounding="half_up")
        half_even = Template(amount_rounding="half_even")
        self.assertEqual(half_up.convert_amount("2.345"), 2.35)
        self.assertEqual(half_even.convert_amount("2.345"), 2.34)
        decimal_template = Template(amount_precision=3, amount_as_decimal=True)
        self.assertEqual(
            decimal_template.convert_amount("1,234.5678"), Decimal("1234.568")
        )

    def test_bad_amount_carries_field_and_raw_value(self):
        template = Template()
        with self.assertRaises(FieldFormatError) as ctx:
            template.convert_amount("abc")
        self.assertEqual(ctx.exception.field_name, "amount")
        self.assertEqual(ctx.exception.value, "abc")

    def test_field_map_keeps_templates_from_shifting_columns(self):
        template = Template(field_map={
            "date": "报销日期", "amount": "金额(元)", "note": "说明",
        })
        row = {"id": 1, "报销日期": "2026-03-01", "金额(元)": "88.8", "说明": "差旅"}
        normalized = template.normalize(row)
        self.assertEqual(normalized["date"], "2026-03-01")
        self.assertEqual(normalized["amount"], 88.8)
        self.assertEqual(normalized["note"], "差旅")


if __name__ == "__main__":
    unittest.main()
