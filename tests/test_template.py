import unittest

from ruletemplate import (
    ConflictError,
    RuleTemplate,
    apply,
    backtest,
    bind_dataset,
    clear_registry,
    migrate_dataset,
    register_template,
)


class TemplateTest(unittest.TestCase):
    def test_returns_given_rules(self):
        rules = [{"name": "not_null", "field": "id"}]
        self.assertEqual(apply("orders", rules)["rules"], rules)

    def test_no_template_key(self):
        result = apply("orders", [{"name": "not_null", "field": "id"}])
        self.assertNotIn("template", result)

    def test_duplicate_names_kept(self):
        rules = [{"name": "range", "field": "qty", "max": 10},
                 {"name": "range", "field": "qty", "max": 20}]
        self.assertEqual(len(apply("orders", rules)["rules"]), 2)


class TemplateCase(unittest.TestCase):
    def setUp(self):
        clear_registry()
        register_template(RuleTemplate("common", 1, [
            {"name": "not_null", "field": "id"},
            {"name": "range", "field": "amount", "min": 0},
        ]))

    def tearDown(self):
        clear_registry()


class TemplateInheritanceTest(TemplateCase):
    def test_child_inherits_parent_rules(self):
        register_template(RuleTemplate("trade", 1, [
            {"name": "not_null", "field": "created_at"},
        ], parent=RuleTemplate("common", 1, [
            {"name": "not_null", "field": "id"},
        ])))
        bind_dataset("orders", "trade")
        fields = {(r["field"], r["name"]) for r in apply("orders", [])["rules"]}
        self.assertEqual(fields, {("id", "not_null"), ("created_at", "not_null")})

    def test_child_overrides_parent_by_field_and_name(self):
        parent = RuleTemplate("common", 1, [
            {"name": "range", "field": "amount", "min": 0},
        ])
        register_template(RuleTemplate("trade", 1, [
            {"name": "range", "field": "amount", "min": 0, "max": 100},
        ], parent=parent))
        bind_dataset("orders", "trade")
        rules = apply("orders", [])["rules"]
        self.assertEqual(rules, [{"name": "range", "field": "amount",
                                  "min": 0, "max": 100}])
        actions = [(e["source"], e["action"])
                   for e in apply("orders", [])["override_chain"]]
        self.assertEqual(actions, [("template:common@1", "define"),
                                   ("template:trade@1", "override")])


class FieldBindingTest(TemplateCase):
    def test_logical_fields_bound_to_dataset_fields(self):
        bind_dataset("orders", "common",
                     fields={"id": "order_id", "amount": "total"})
        rules = apply("orders", [])["rules"]
        self.assertEqual(rules, [
            {"name": "not_null", "field": "order_id"},
            {"name": "range", "field": "total", "min": 0},
        ])

    def test_same_template_shared_by_two_datasets(self):
        bind_dataset("orders", "common", fields={"id": "order_id"})
        bind_dataset("users", "common", fields={"id": "user_id"})
        self.assertEqual(apply("orders", [])["rules"][0]["field"], "order_id")
        self.assertEqual(apply("users", [])["rules"][0]["field"], "user_id")


class OverrideTest(TemplateCase):
    def test_binding_override_matches_by_field_and_name(self):
        bind_dataset("orders", "common", overrides=[
            {"name": "range", "field": "amount", "min": 0, "max": 1000},
        ])
        rules = apply("orders", [])["rules"]
        self.assertEqual(rules[1], {"name": "range", "field": "amount",
                                    "min": 0, "max": 1000})

    def test_binding_can_add_new_rule(self):
        bind_dataset("orders", "common", overrides=[
            {"name": "not_null", "field": "created_at"},
        ])
        fields = {(r["field"], r["name"]) for r in apply("orders", [])["rules"]}
        self.assertIn(("created_at", "not_null"), fields)

    def test_call_rules_override_binding(self):
        bind_dataset("orders", "common", overrides=[
            {"name": "range", "field": "amount", "min": 0, "max": 1000},
        ])
        result = apply("orders", [
            {"name": "range", "field": "amount", "min": 5, "max": 50}])
        self.assertEqual(result["rules"][1],
                         {"name": "range", "field": "amount", "min": 5, "max": 50})
        sources = [e["source"] for e in result["override_chain"]]
        self.assertEqual(sources[-2:], ["dataset:orders", "call:orders"])

    def test_override_without_field_filled_when_unique(self):
        bind_dataset("orders", "common", overrides=[
            {"name": "range", "min": 0, "max": 1000},
        ])
        rules = apply("orders", [])["rules"]
        self.assertEqual(rules[1], {"name": "range", "field": "amount",
                                    "min": 0, "max": 1000})


class ConflictTest(TemplateCase):
    def test_duplicate_rules_in_template_rejected_at_load(self):
        with self.assertRaises(ConflictError) as ctx:
            register_template(RuleTemplate("bad", 1, [
                {"name": "not_null", "field": "id"},
                {"name": "not_null", "field": "id"},
            ]))
        self.assertEqual(ctx.exception.template, "bad")
        self.assertEqual(ctx.exception.field, "id")
        self.assertEqual(ctx.exception.source, "template:bad")

    def test_duplicate_overrides_rejected_at_bind(self):
        with self.assertRaises(ConflictError) as ctx:
            bind_dataset("orders", "common", overrides=[
                {"name": "range", "field": "amount", "min": 0},
                {"name": "range", "field": "amount", "min": 1},
            ])
        self.assertEqual(ctx.exception.template, "common")
        self.assertEqual(ctx.exception.field, "amount")
        self.assertEqual(ctx.exception.source, "dataset:orders")

    def test_ambiguous_override_without_field_rejected(self):
        register_template(RuleTemplate("multi", 1, [
            {"name": "not_null", "field": "id"},
            {"name": "not_null", "field": "created_at"},
        ]))
        with self.assertRaises(ConflictError) as ctx:
            bind_dataset("orders", "multi", overrides=[{"name": "not_null"}])
        self.assertEqual(ctx.exception.template, "multi")
        self.assertIsNone(ctx.exception.field)
        self.assertEqual(ctx.exception.source, "dataset:orders")


class MigrationTest(TemplateCase):
    def _register_v2(self):
        register_template(RuleTemplate("common", 2, [
            {"name": "range", "field": "amount", "min": 0, "max": 100000},
            {"name": "not_null", "field": "created_at"},
        ], parent=self._v1()))

    def _v1(self):
        return RuleTemplate("common", 1, [
            {"name": "not_null", "field": "id"},
            {"name": "range", "field": "amount", "min": 0},
        ])

    def test_apply_reports_migration_diff_when_behind(self):
        bind_dataset("orders", "common", version=1)
        self._register_v2()
        migration = apply("orders", [])["migration"]
        self.assertEqual((migration["from"], migration["to"]), (1, 2))
        self.assertEqual([r["field"] for r in migration["added"]], ["created_at"])
        self.assertEqual([c["name"] for c in migration["changed"]], ["range"])

    def test_migrate_dataset_updates_binding(self):
        bind_dataset("orders", "common", version=1)
        self._register_v2()
        result = migrate_dataset("orders", 2)
        self.assertEqual((result["from"], result["to"]), (1, 2))
        self.assertIsNone(apply("orders", [])["migration"])
        fields = {(r["field"], r["name"]) for r in apply("orders", [])["rules"]}
        self.assertIn(("created_at", "not_null"), fields)


class BacktestTest(TemplateCase):
    def test_backtest_counts_pass_and_fail(self):
        bind_dataset("orders", "common", fields={"id": "order_id"})
        rows = [{"order_id": 1, "amount": 10},
                {"order_id": None, "amount": -3}]
        report = backtest("orders", rows)
        by_name = {r["name"]: r for r in report["results"]}
        self.assertEqual((by_name["not_null"]["passed"],
                          by_name["not_null"]["failed"]), (1, 1))
        self.assertEqual((by_name["range"]["passed"],
                          by_name["range"]["failed"]), (1, 1))

    def test_migrate_with_rows_reports_impact(self):
        register_template(RuleTemplate("common", 2, [
            {"name": "range", "field": "amount", "min": 0, "max": 100},
        ], parent=RuleTemplate("common", 1, [
            {"name": "not_null", "field": "id"},
            {"name": "range", "field": "amount", "min": 0},
        ])))
        bind_dataset("orders", "common", version=1)
        rows = [{"id": 1, "amount": 10}, {"id": 2, "amount": 500}]
        result = migrate_dataset("orders", 2, rows=rows)
        impact = {i["name"]: i for i in result["backtest"]["impact"]}
        self.assertEqual(impact["range"]["before_failed"], 0)
        self.assertEqual(impact["range"]["after_failed"], 1)
