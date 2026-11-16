import unittest

from logmask import mask, report


class MaskTest(unittest.TestCase):
    def test_phone_is_fully_replaced(self):
        result = mask({"service": "pay", "phone": "13800001111"})
        self.assertEqual(result["record"]["phone"], "***")

    def test_business_number_is_wiped(self):
        result = mask({"service": "pay", "order_no": "13800001234"})
        self.assertEqual(result["record"]["order_no"], "***")

    def test_unlisted_field_is_masked_the_same_way(self):
        result = mask({"service": "pay", "device": "13900002222"})
        self.assertEqual(result["record"]["device"], "***")
        self.assertEqual(result["unregistered"], [])

    def test_no_trace_id_is_returned(self):
        result = mask({"service": "pay", "phone": "13800001111"})
        self.assertIsNone(result["trace_id"])

    def test_report_counts_masked_fields(self):
        mask({"service": "order", "phone": "13800001111"})
        body = report()
        self.assertEqual(body["services"]["order"], 1)
        self.assertEqual(body["unregistered"], 0)
