import unittest
from unittest import mock

from fieldpolicy import grant, resolve
from fieldpolicy import policy


class PolicyTest(unittest.TestCase):
    def setUp(self):
        policy._global["masked"] = False

    def test_global_applies(self):
        policy._global["masked"] = True
        self.assertTrue(resolve("orders", "phone")["masked"])

    def test_grant_not_active(self):
        self.assertFalse(grant("orders", "phone", 100)["active"])

    def test_no_inheritance(self):
        self.assertNotIn("source", resolve("orders", "phone"))


class InheritanceTest(unittest.TestCase):
    def setUp(self):
        policy._reset()

    def tearDown(self):
        policy._reset()

    def test_four_layer_source(self):
        policy.set_global(True)
        self.assertEqual(resolve("sales.orders", "phone")["source_layer"], "global")

        policy._reset()
        policy.set_domain("sales", True)
        self.assertEqual(resolve("sales.orders", "phone")["source_layer"], "domain")

        policy.set_table("sales.orders", True)
        self.assertEqual(resolve("sales.orders", "phone")["source_layer"], "table")

        policy.set_field("sales.orders", "phone", True)
        result = resolve("sales.orders", "phone")
        self.assertEqual(result["source_layer"], "field")
        self.assertTrue(result["masked"])

    def test_lower_layer_tightens_only(self):
        policy.set_field("orders", "phone", True)
        self.assertTrue(resolve("orders", "phone")["masked"])
        self.assertFalse(resolve("orders", "email")["masked"])
        self.assertFalse(resolve("users", "phone")["masked"])

    def test_lower_layer_cannot_loosen(self):
        policy.set_global(True)
        with self.assertRaises(ValueError):
            policy.set_domain("sales", False)
        with self.assertRaises(ValueError):
            policy.set_table("orders", False)
        with self.assertRaises(ValueError):
            policy.set_field("orders", "phone", False)
        policy.set_table("orders", True)
        self.assertTrue(resolve("orders", "phone")["masked"])

    def test_table_cannot_loosen_domain(self):
        policy.set_domain("sales", True)
        with self.assertRaises(ValueError):
            policy.set_table("sales.orders", False)
        with self.assertRaises(ValueError):
            policy.set_field("sales.orders", "phone", False)


class ExemptionTest(unittest.TestCase):
    def setUp(self):
        policy._reset()

    def tearDown(self):
        policy._reset()

    def test_active_exemption_unmasks(self):
        policy.set_field("orders", "phone", True)
        future = policy.time.time() + 3600
        result = grant("orders", "phone", future, approver="alice")
        self.assertTrue(result["active"])
        resolved = resolve("orders", "phone")
        self.assertFalse(resolved["masked"])
        self.assertEqual(resolved["exemption"]["approver"], "alice")
        self.assertEqual(resolved["exemption"]["until"], future)

    def test_expired_exemption_auto_invalidates_and_audits(self):
        policy.set_field("orders", "phone", True)
        now = policy.time.time()
        grant("orders", "phone", now + 60, approver="alice")
        with mock.patch.object(policy.time, "time", return_value=now + 120):
            resolved = resolve("orders", "phone")
        self.assertTrue(resolved["masked"])
        self.assertIsNone(resolved["exemption"])
        log = policy.audit_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["event"], "exemption_expired")
        self.assertEqual(log[0]["approver"], "alice")

    def test_expired_grant_returns_inactive(self):
        result = grant("orders", "phone", 100, approver="bob")
        self.assertFalse(result["active"])
        self.assertEqual(len(policy.audit_log()), 1)


class WriteOutTest(unittest.TestCase):
    def setUp(self):
        policy._reset()

    def tearDown(self):
        policy._reset()

    def test_write_out_masks_policy_fields(self):
        policy.set_field("orders", "phone", True)
        row = policy.write_out("orders", {"phone": "13800000000", "city": "sz"})
        self.assertEqual(row["phone"], policy.MASK)
        self.assertEqual(row["city"], "sz")

    def test_write_out_respects_active_exemption(self):
        policy.set_field("orders", "phone", True)
        grant("orders", "phone", policy.time.time() + 3600, approver="alice")
        row = policy.write_out("orders", {"phone": "13800000000"})
        self.assertEqual(row["phone"], "13800000000")

    def test_write_out_intercepts_expired_exemption(self):
        policy.set_field("orders", "phone", True)
        now = policy.time.time()
        grant("orders", "phone", now + 60, approver="alice")
        with mock.patch.object(policy.time, "time", return_value=now + 120):
            row = policy.write_out("orders", {"phone": "13800000000"})
        self.assertEqual(row["phone"], policy.MASK)
