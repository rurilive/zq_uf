import unittest

from datacontract import BreakingChangeError, Contract, Registry


class DatacontractTest(unittest.TestCase):
    def test_register(self):
        registry = Registry()
        self.assertEqual(registry.register({"name": "orders", "fields": ["id"]}), ["id"])

    def test_diff_added(self):
        registry = Registry()
        self.assertEqual(registry.diff(["id"], ["id", "amount"]), {"added": ["amount"], "removed": []})

    def test_diff_removed(self):
        registry = Registry()
        self.assertEqual(registry.diff(["id", "note"], ["id"]), {"added": [], "removed": ["note"]})


def make_contract(version, fields, consumers=None):
    return Contract("orders", version, fields, consumers=consumers)


BASE_FIELDS = [
    {"name": "id", "type": "int", "nullable": False},
    {"name": "amount", "type": "float", "nullable": False},
    {"name": "status", "type": "str", "enum": ["paid", "shipped"]},
]

CONSUMERS = {"billing": ["id", "amount"], "search": None}


class ValidateTest(unittest.TestCase):
    def test_validate_reports_line_numbers(self):
        registry = Registry()
        registry.register(make_contract("1.0", BASE_FIELDS))
        rows = [
            {"id": 1, "amount": 9.9, "status": "paid"},
            {"id": "x", "amount": 1.0, "status": "unknown"},
            {"amount": 2.0, "status": "shipped"},
        ]
        errors = registry.validate("orders", rows)
        self.assertEqual([e["line"] for e in errors], [2, 2, 3])
        self.assertEqual(errors[0]["field"], "id")
        self.assertEqual(errors[1]["field"], "status")
        self.assertEqual(errors[2]["field"], "id")


class DiffTest(unittest.TestCase):
    def test_type_tightening_is_breaking(self):
        old = make_contract("1.0", BASE_FIELDS)
        new = make_contract("2.0", [
            {"name": "id", "type": "int", "nullable": False},
            {"name": "amount", "type": "int", "nullable": False},
            {"name": "status", "type": "str", "enum": ["paid", "shipped"]},
        ])
        report = Registry().diff(old, new)
        self.assertEqual(report["verdict"], "breaking")
        self.assertEqual(report["breaking"][0]["field"], "amount")
        self.assertEqual(report["breaking"][0]["change"], "type_tightened")

    def test_field_removed_is_breaking_and_lists_consumers(self):
        old = make_contract("1.0", BASE_FIELDS, consumers=CONSUMERS)
        new = make_contract("2.0", BASE_FIELDS[:1] + BASE_FIELDS[2:])
        report = Registry().diff(old, new)
        self.assertEqual(report["verdict"], "breaking")
        entry = report["breaking"][0]
        self.assertEqual(entry["field"], "amount")
        self.assertEqual(entry["change"], "field_removed")
        self.assertEqual(entry["consumers"], ["billing", "search"])

    def test_enum_added_is_compatible_with_hint(self):
        old = make_contract("1.0", BASE_FIELDS)
        new = make_contract("1.1", [
            {"name": "id", "type": "int", "nullable": False},
            {"name": "amount", "type": "float", "nullable": False},
            {"name": "status", "type": "str", "enum": ["paid", "shipped", "refunded"]},
        ])
        report = Registry().diff(old, new)
        self.assertEqual(report["verdict"], "compatible")
        self.assertEqual(report["breaking"], [])
        self.assertEqual(report["hints"][0]["change"], "enum_values_added")

    def test_enum_value_removed_is_breaking(self):
        old = make_contract("1.0", BASE_FIELDS)
        new = make_contract("2.0", BASE_FIELDS[:2] + [
            {"name": "status", "type": "str", "enum": ["paid"]},
        ])
        report = Registry().diff(old, new)
        self.assertEqual(report["verdict"], "breaking")
        self.assertEqual(report["breaking"][0]["change"], "enum_values_removed")


class RegistryFlowTest(unittest.TestCase):
    def test_breaking_register_blocked_by_default(self):
        registry = Registry()
        registry.register(make_contract("1.0", BASE_FIELDS))
        breaking = make_contract("2.0", BASE_FIELDS[:2])
        with self.assertRaises(BreakingChangeError):
            registry.register(breaking)
        self.assertEqual(registry.current("orders").version, "1.0")
        registry.register(breaking, allow_breaking=True)
        self.assertEqual(registry.current("orders").version, "2.0")

    def test_check_does_not_register(self):
        registry = Registry()
        registry.register(make_contract("1.0", BASE_FIELDS))
        report = registry.check(make_contract("2.0", BASE_FIELDS[:2]))
        self.assertEqual(report["verdict"], "breaking")
        self.assertEqual(registry.history("orders"), ["1.0"])

    def test_rollback(self):
        registry = Registry()
        registry.register(make_contract("1.0", BASE_FIELDS))
        registry.register(make_contract("2.0", BASE_FIELDS[:2]), allow_breaking=True)
        self.assertEqual(registry.history("orders"), ["1.0", "2.0"])
        contract = registry.rollback("orders", "1.0")
        self.assertEqual(contract.version, "1.0")
        self.assertEqual(registry.current("orders").version, "1.0")
        with self.assertRaises(KeyError):
            registry.rollback("orders", "9.9")

    def test_for_consumer(self):
        registry = Registry()
        registry.register(make_contract("1.0", BASE_FIELDS, consumers=CONSUMERS))
        billing = registry.for_consumer("billing")
        self.assertEqual(billing["orders"]["fields"], ["id", "amount"])
        self.assertEqual(billing["orders"]["version"], "1.0")
        search = registry.for_consumer("search")
        self.assertEqual(search["orders"]["fields"], ["id", "amount", "status"])
        self.assertEqual(registry.for_consumer("nobody"), {})


if __name__ == "__main__":
    unittest.main()
