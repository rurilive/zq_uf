import unittest

from surfacemap import report, reset, surface

ENTRY = {
    "domain": "gw.example.com",
    "address": "10.2.0.9",
    "port": 443,
    "owner": "pay",
    "source": "cmdb",
}


class EntriesTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_same_entry_registered_twice(self):
        surface([ENTRY])
        surface([ENTRY])
        self.assertEqual(report()["total"], 2)

    def test_offline_entry_still_active(self):
        surface([dict(ENTRY, domain="old.example.com", offline=True)])
        row = report()["entries"][0]
        self.assertEqual(row["status"], "active")
        self.assertNotIn("offline_at", row)

    def test_owner_counted_per_row(self):
        surface([ENTRY, dict(ENTRY, domain="api.example.com", address="10.2.0.10")])
        self.assertEqual(report()["by_owner"], {"pay": 2})

    def test_port_defaults_to_443(self):
        surface([{"domain": "cdn.example.com", "address": "10.2.0.11"}])
        self.assertEqual(report()["entries"][0]["port"], 443)

    def test_missing_address_raises(self):
        with self.assertRaises(ValueError):
            surface([{"domain": "cdn.example.com"}])


if __name__ == "__main__":
    unittest.main()
