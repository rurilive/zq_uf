import unittest

from riskrule import (
    ConfigError,
    NotFoundError,
    activate,
    active_config,
    compare,
    configure,
    evaluate,
    explain,
    history,
    replay,
    reset,
    validate,
)


class RulesTest(unittest.TestCase):
    def setUp(self):
        reset()

    # ---- 基本行为：配置化、命中解释、取值 ----

    def test_amount_rule_denies(self):
        got = evaluate({"id": "e1", "amount": 1500})
        self.assertEqual(got["action"], "deny")
        self.assertEqual(got["rule"], "r1")

    def test_no_match_allows(self):
        got = evaluate({"id": "e3", "amount": 10, "country": "JP", "channel": "app"})
        self.assertEqual(got["action"], "allow")
        self.assertEqual(got["rule"], "")
        self.assertEqual(got["matched"], [])

    def test_values_returned_for_matches(self):
        got = evaluate({"id": "e4", "amount": 2000, "country": "RU"})
        self.assertEqual(got["values"], {"amount": 2000, "country": "RU"})
        self.assertEqual([item["rule"] for item in got["matched"]], ["r1", "r2"])
        self.assertEqual(got["matched"][0]["actual"], 2000)
        self.assertEqual(got["matched"][0]["condition"], "amount >= 1000")

    def test_event_must_be_dict(self):
        with self.assertRaises(ValueError):
            evaluate(["amount", 1500])

    def test_missing_field_does_not_match(self):
        got = evaluate({"id": "e5", "country": "RU"})
        self.assertEqual(got["rule"], "r2")
        got = evaluate({"id": "e6"})
        self.assertEqual(got["action"], "allow")

    def test_number_rule_ignores_non_numeric(self):
        got = evaluate({"id": "e7", "amount": "1500"})
        self.assertEqual(got["action"], "allow")

    def test_bool_is_not_a_number(self):
        got = evaluate({"id": "e8", "amount": True})
        self.assertEqual(got["action"], "allow")

    # ---- 优先级 ----

    def test_priority_beats_config_order(self):
        configure(
            {
                "version": "v2",
                "rules": [
                    {"id": "low", "priority": 1, "condition": {"field": "amount", "op": ">="},
                     "threshold": 100, "action": "review"},
                    {"id": "high", "priority": 9, "condition": {"field": "amount", "op": ">="},
                     "threshold": 100, "action": "deny"},
                ],
            }
        )
        got = evaluate({"id": "e1", "amount": 500})
        self.assertEqual(got["action"], "deny")
        self.assertEqual(got["rule"], "high")
        self.assertEqual([item["rule"] for item in got["matched"]], ["high", "low"])

    def test_same_priority_falls_back_to_config_order(self):
        configure(
            {
                "version": "v2",
                "rules": [
                    {"id": "a", "priority": 5, "condition": {"field": "country", "op": "=="},
                     "value": "RU", "action": "review"},
                    {"id": "b", "priority": 5, "condition": {"field": "country", "op": "in"},
                     "value": ["RU"], "action": "deny"},
                ],
            }
        )
        got = evaluate({"id": "e1", "country": "RU"})
        self.assertEqual(got["rule"], "a")
        self.assertEqual(got["action"], "review")

    def test_priority_conflict_reported_before_execution(self):
        warnings = validate(
            {
                "rules": [
                    {"id": "a", "priority": 5, "condition": {"field": "amount", "op": ">="},
                     "threshold": 100, "action": "deny"},
                    {"id": "b", "priority": 5, "condition": {"field": "amount", "op": "<"},
                     "threshold": 1000, "action": "review"},
                ]
            }
        )
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["type"], "priority_conflict")
        self.assertEqual(warnings[0]["rules"], ["a", "b"])

    def test_disjoint_conditions_are_not_conflicts(self):
        warnings = validate(
            {
                "rules": [
                    {"id": "a", "priority": 5, "condition": {"field": "amount", "op": ">="},
                     "threshold": 1000, "action": "deny"},
                    {"id": "b", "priority": 5, "condition": {"field": "amount", "op": "<"},
                     "threshold": 1000, "action": "review"},
                ]
            }
        )
        self.assertEqual(warnings, [])

    def test_different_fields_are_not_conflicts(self):
        warnings = validate(
            {
                "rules": [
                    {"id": "a", "priority": 5, "condition": {"field": "amount", "op": ">="},
                     "threshold": 1000, "action": "deny"},
                    {"id": "b", "priority": 5, "condition": {"field": "country", "op": "=="},
                     "value": "RU", "action": "deny"},
                ]
            }
        )
        self.assertEqual(warnings, [])

    def test_runtime_conflict_when_tied_rules_both_hit(self):
        configure(
            {
                "version": "v2",
                "rules": [
                    {"id": "a", "priority": 5, "condition": {"field": "country", "op": "=="},
                     "value": "RU", "action": "review"},
                    {"id": "b", "priority": 5, "condition": {"field": "country", "op": "=="},
                     "value": "RU", "action": "deny"},
                ],
            }
        )
        got = evaluate({"id": "e1", "country": "RU"})
        self.assertEqual(len(got["conflicts"]), 1)
        self.assertEqual(got["conflicts"][0]["resolved_by"], "config_order")

    # ---- 配置与版本 ----

    def test_configure_takes_effect(self):
        configure(
            {
                "version": "v2",
                "rules": [
                    {"id": "r1", "priority": 1, "condition": {"field": "amount", "op": ">="},
                     "threshold": 5000, "action": "deny"}
                ],
            }
        )
        self.assertEqual(evaluate({"id": "e1", "amount": 1500})["action"], "allow")
        self.assertEqual(evaluate({"id": "e2", "amount": 6000})["action"], "deny")
        self.assertEqual(active_config()["version"], "v2")

    def test_old_versions_are_kept(self):
        configure({"version": "v2", "rules": []})
        versions = [item["version"] for item in history()]
        self.assertEqual(versions, ["v1", "v2"])
        with self.assertRaises(ConfigError):
            configure({"version": "v2", "rules": []})
        self.assertEqual([item["version"] for item in history()], ["v1", "v2"])

    def test_activate_old_version(self):
        configure({"version": "v2", "rules": []})
        activate("v1")
        self.assertEqual(evaluate({"id": "e1", "amount": 1500})["action"], "deny")
        activate("v2")
        self.assertEqual(evaluate({"id": "e2", "amount": 1500})["action"], "allow")
        with self.assertRaises(NotFoundError):
            activate("nope")

    def test_inline_config_is_not_saved(self):
        got = evaluate(
            {"id": "e1", "amount": 1500},
            config=[{"id": "x", "condition": {"field": "amount", "op": ">="},
                     "threshold": 9999, "action": "deny"}],
        )
        self.assertEqual(got["action"], "allow")
        self.assertEqual([item["version"] for item in history()], ["v1"])

    def test_invalid_config(self):
        bad_configs = [
            {"rules": [{"condition": {"field": "amount", "op": ">="}, "threshold": 1}]},
            {"rules": [{"id": "a", "condition": {"field": "amount", "op": "??"}, "value": 1}]},
            {"rules": [{"id": "a", "condition": {"field": "amount", "op": ">="}}]},
            {"rules": [{"id": "a", "condition": {"field": "amount", "op": ">="},
                        "threshold": 1, "action": "explode"}]},
            {"rules": [{"id": "a", "condition": {"field": "x", "op": "in"}, "value": []}]},
        ]
        for config in bad_configs:
            with self.assertRaises(ConfigError):
                validate(config)

    def test_duplicate_rule_ids_rejected(self):
        with self.assertRaises(ConfigError):
            validate(
                {
                    "rules": [
                        {"id": "a", "condition": {"field": "amount", "op": ">="}, "threshold": 1},
                        {"id": "a", "condition": {"field": "amount", "op": ">="}, "threshold": 2},
                    ]
                }
            )

    def test_flat_rule_form_is_accepted(self):
        got = evaluate(
            {"id": "e1", "amount": 10},
            config=[{"id": "f", "field": "amount", "op": "<", "threshold": 100, "action": "review"}],
        )
        self.assertEqual((got["action"], got["rule"]), ("review", "f"))

    # ---- 回放 ----

    def test_replay_gives_distribution(self):
        for index, amount in enumerate([50, 150, 250, 3500]):
            evaluate({"id": "e%d" % index, "amount": amount})
        summary = replay()
        self.assertEqual(summary["events"], 4)
        self.assertEqual(summary["actions"], {"allow": 3, "deny": 1, "review": 0})
        self.assertAlmostEqual(summary["pass_rate"], 0.75)
        self.assertEqual(summary["hits"], {"r1": 1})

    def test_replay_other_version_keeps_history(self):
        evaluate({"id": "e1", "amount": 1500})
        configure(
            {
                "version": "v2",
                "rules": [
                    {"id": "r1", "priority": 1, "condition": {"field": "amount", "op": ">="},
                     "threshold": 2000, "action": "deny"}
                ],
            }
        )
        self.assertEqual(replay("v1")["actions"]["deny"], 1)
        self.assertEqual(replay("v2")["actions"]["allow"], 1)

    def test_replay_time_window(self):
        from riskrule import store

        evaluate({"id": "new", "amount": 10})
        store.add(
            "old",
            {"id": "old", "amount": 1500},
            "v1",
            {"action": "deny", "rule": "r1", "matched": [], "values": {}},
            at="2026-01-01T00:00:00Z",
        )
        window = replay(start="2026-06-01T00:00:00Z", end="2026-12-31T00:00:00Z")
        self.assertEqual(window["events"], 1)
        self.assertEqual(window["by_event"][0]["event"], "new")
        self.assertEqual(replay(start="2026-01-01T00:00:00Z", end="2026-01-31T00:00:00Z")["by_event"][0]["event"], "old")

    def test_replay_with_explicit_events(self):
        summary = replay(events=[{"id": "x", "amount": 1500}, {"id": "y", "amount": 1}])
        self.assertEqual(summary["events"], 2)

    def test_repeated_event_counts_once_in_replay(self):
        evaluate({"id": "e1", "amount": 1500})
        evaluate({"id": "e1", "amount": 1500})
        self.assertEqual(replay()["events"], 1)
        self.assertEqual(evaluate({"id": "e1", "amount": 1500})["rule"], "r1")
        self.assertEqual(replay()["events"], 1)

    # ---- 对比 ----

    def test_compare_pass_rate_and_changes(self):
        for index, amount in enumerate([900, 1200, 300, 1600]):
            evaluate({"id": "e%d" % index, "amount": amount})
        configure(
            {
                "version": "v2",
                "rules": [
                    {"id": "r1", "priority": 1, "condition": {"field": "amount", "op": ">="},
                     "threshold": 2000, "action": "deny"}
                ],
            }
        )
        diff = compare("v1", "v2")
        self.assertEqual(diff["events"], 4)
        self.assertEqual(diff["pass_rate"]["from"], 0.5)
        self.assertEqual(diff["pass_rate"]["to"], 1.0)
        self.assertAlmostEqual(diff["pass_rate"]["delta"], 0.5)
        self.assertEqual(diff["changed_count"], 2)
        self.assertEqual({item["from_rule"] for item in diff["changed"]}, {"r1"})

    def test_compare_defaults_to_active(self):
        evaluate({"id": "e1", "amount": 1500})
        configure({"version": "v2", "rules": []})
        diff = compare("v1")
        self.assertEqual(diff["to"], "v2")
        self.assertEqual(diff["changed_count"], 1)

    # ---- 解释 ----

    def test_explain_rule_reports_definition_and_replay(self):
        evaluate({"id": "e1", "amount": 1500})
        got = explain("r1")
        self.assertEqual(got["type"], "rule")
        self.assertEqual(got["threshold"], 1000)
        self.assertEqual(got["condition"], "amount >= 1000")
        self.assertEqual(got["replay"]["rule_hits"], 1)
        self.assertEqual(got["events"], [{"event": "e1", "action": "deny", "rule": "r1"}])

    def test_explain_event_returns_values_and_replay(self):
        evaluate({"id": "e1", "amount": 1500, "country": "JP"})
        got = explain("e1")
        self.assertEqual(got["type"], "event")
        self.assertEqual(got["action"], "deny")
        self.assertEqual(got["values"], {"amount": 1500})
        self.assertEqual(got["snapshot"]["amount"], 1500)
        self.assertEqual(got["recorded"]["rule"], "r1")
        self.assertEqual(got["replay"]["events"], 1)
        self.assertEqual([item["rule"] for item in got["matched"]], ["r1"])

    def test_explain_event_detects_rejudgement(self):
        evaluate({"id": "e1", "amount": 1500})
        configure({"version": "v2", "rules": []})
        got = explain("e1")
        self.assertTrue(got["changed"])
        self.assertEqual(got["recorded"]["rule"], "r1")
        self.assertEqual(got["action"], "allow")

    def test_explain_unknown_id(self):
        with self.assertRaises(NotFoundError):
            explain("missing")

    def test_explain_empty_id(self):
        with self.assertRaises(ValueError):
            explain("")

    def test_explain_kind_disambiguates(self):
        evaluate({"id": "r1", "amount": 10})
        self.assertEqual(explain("r1")["type"], "rule")
        self.assertEqual(explain("r1", kind="event")["type"], "event")

    def test_explain_respects_version(self):
        evaluate({"id": "e1", "amount": 1500})
        configure(
            {
                "version": "v2",
                "rules": [
                    {"id": "r1", "priority": 1, "condition": {"field": "amount", "op": ">="},
                     "threshold": 2000, "action": "deny"}
                ],
            }
        )
        got = explain("r1", config="v2")
        self.assertEqual(got["threshold"], 2000)
        self.assertEqual(got["replay"]["rule_hits"], 0)

    # ---- 记录 ----

    def test_recording_can_be_disabled(self):
        evaluate({"id": "e1", "amount": 1500}, record=False)
        with self.assertRaises(NotFoundError):
            explain("e1")

    def test_reset_clears_records_and_versions(self):
        evaluate({"id": "e1", "amount": 1500})
        configure({"version": "v2", "rules": []})
        result = reset()
        self.assertEqual(result["events"], 1)
        self.assertEqual([item["version"] for item in history()], ["v1"])
        with self.assertRaises(NotFoundError):
            explain("e1")


if __name__ == "__main__":
    unittest.main()
