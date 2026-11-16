import unittest

from tlsc import check, expiring, report


class ProbeTest(unittest.TestCase):
    def test_weak_protocol_flagged(self):
        out = check("shop.example.com")
        self.assertTrue(out["weak"])
        self.assertEqual(out["protocol"], "TLSv1.0")

    def test_expire_soon_by_days(self):
        self.assertTrue(check("shop.example.com")["expire_soon"])
        self.assertFalse(check("api.example.com")["expire_soon"])

    def test_redirect_not_reported(self):
        out = check("api.example.com")
        self.assertNotIn("redirect", out)
        self.assertNotIn("chain", out)

    def test_report_lists_weak_and_expiring(self):
        out = report()
        self.assertEqual(out["hosts"], ["api.example.com", "cdn.example.com", "shop.example.com"])
        self.assertEqual(out["weak"], ["api.example.com", "shop.example.com"])
        self.assertEqual(out["expiring"], ["shop.example.com"])

    def test_unknown_host_raises(self):
        with self.assertRaises(ValueError):
            check("mail.example.com")
        self.assertEqual(expiring(60)["hosts"], ["cdn.example.com", "shop.example.com"])


if __name__ == "__main__":
    unittest.main()
