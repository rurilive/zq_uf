import unittest

from formfill import normalize_row, submit_batch


class FormfillTest(unittest.TestCase):
    def test_amount_rounding(self):
        row = normalize_row({"id": 1, "date": "2026-03-01", "amount": "12.345"})
        self.assertEqual(row["amount"], 12.35)

    def test_date_normalized(self):
        row = normalize_row({"id": 2, "date": "2026-3-1", "amount": "1"})
        self.assertEqual(row["date"], "2026-03-01")

    def test_order_preserved(self):
        rows = [
            {"id": 1, "date": "2026-03-01", "amount": "1"},
            {"id": 2, "date": "2026-03-02", "amount": "2"},
        ]
        receipts = submit_batch(rows)
        self.assertEqual([item.business_key for item in receipts], ["1", "2"])

    def test_status_passthrough(self):
        rows = [{"id": 3, "date": "2026-03-03", "amount": "3"}]
        receipts = submit_batch(rows, lambda row: {"status": "rejected", "message": "金额超限"})
        self.assertEqual(receipts[0].status, "rejected")
        self.assertEqual(receipts[0].message, "金额超限")
