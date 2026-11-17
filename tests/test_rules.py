import unittest

from riskrule import (RuleConflictError, evaluate, explain, get_rules,
                      list_versions, load_rules, replay, reset, validate_rules)


class RulesTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_amount_rule_denies(self):
        got = evaluate({"id": "e1", "amount": 1500})
        self.assertEqual(got["action"], "deny")
        self.assertEqual(got["hits"][0]["rule"], "r1")

    def test_no_match_allows(self):
        got = evaluate({"id": "e3", "amount": 10, "country": "JP", "channel": "app"})
        self.assertEqual(got["action"], "allow")
        self.assertEqual(got["hits"], [])

    def test_event_must_be_dict(self):
        with self.assertRaises(ValueError):
            evaluate(["amount", 1500])

    def test_hit_returns_rule_and_values(self):
        got = evaluate({"id": "e1", "amount": 1500})
        self.assertEqual(got["hits"][0]["values"], {"amount": 1500})
        self.assertEqual(got["hits"][0]["condition"], "amount >= 1000")

    def test_multi_hit_priority_decides(self):
        # r1/r2 deny(100) 与 r3 review(10) 同时命中，按优先级取 deny
        got = evaluate({"id": "e2", "amount": 2000, "country": "RU", "channel": "web"})
        self.assertEqual(got["action"], "deny")
        self.assertEqual({h["rule"] for h in got["hits"]}, {"r1", "r2", "r3"})

    def test_priority_conflict_blocks_load(self):
        rules = [
            {"id": "a", "priority": 10, "action": "deny",
             "condition": {"field": "amount", "op": ">=", "value": 100}},
            {"id": "b", "priority": 10, "action": "review",
             "condition": {"field": "amount", "op": ">=", "value": 500}},
        ]
        with self.assertRaises(RuleConflictError) as ctx:
            load_rules(rules)
        self.assertEqual(ctx.exception.warnings[0]["type"], "priority_conflict")
        # 冲突被拒绝后仍是旧版本
        self.assertEqual(len(list_versions()), 1)

    def test_mutually_exclusive_conditions_no_conflict(self):
        rules = [
            {"id": "a", "priority": 10, "action": "deny",
             "condition": {"field": "country", "op": "==", "value": "RU"}},
            {"id": "b", "priority": 10, "action": "review",
             "condition": {"field": "country", "op": "==", "value": "JP"}},
        ]
        self.assertEqual(validate_rules(rules), [])

    def test_versions_kept_for_compare(self):
        rules = get_rules()
        rules[0]["condition"]["value"] = 2000
        load_rules(rules, note="阈值 1000→2000")
        versions = list_versions()
        self.assertEqual([v["version"] for v in versions], [1, 2])
        self.assertEqual(versions[1]["note"], "阈值 1000→2000")
        # 旧版本规则仍可取出对比
        self.assertEqual(get_rules(1)[0]["condition"]["value"], 1000)

    def test_replay_time_range_and_hit_distribution(self):
        evaluate({"id": "e1", "ts": 100, "amount": 1500})
        evaluate({"id": "e2", "ts": 200, "amount": 10, "country": "JP"})
        out = replay(start=150, end=300)
        self.assertEqual(out["total"], 1)  # 只回放受影响的时间段
        out = replay(start=0, end=300)
        self.assertEqual(out["hits"], {"r1": 1})
        self.assertEqual(out["actions"], {"deny": 1, "allow": 1})
        self.assertEqual(out["pass_rate"], 0.5)

    def test_replay_compare_pass_rate_around_threshold_change(self):
        evaluate({"id": "e1", "ts": 100, "amount": 1500})
        evaluate({"id": "e2", "ts": 200, "amount": 500})
        rules = get_rules()
        rules[0]["condition"]["value"] = 2000
        new_v = load_rules(rules, note="阈值 1000→2000")["version"]
        out = replay(start=0, end=300, version=new_v, compare_to=1)
        self.assertEqual(out["pass_rate"], 1.0)            # 调整后
        self.assertEqual(out["compare"]["pass_rate"], 0.5)  # 调整前
        self.assertEqual(out["compare"]["diff"],
                         [{"event": "e1", "before": "deny", "after": "allow"}])
        self.assertEqual(out["changed"],
                         [{"event": "e1", "before": "deny", "after": "allow"}])

    def test_explain_returns_values_and_replay(self):
        evaluate({"id": "e1", "ts": 100, "amount": 1500, "country": "RU"})
        replay(start=0, end=200)
        got = explain("e1")
        self.assertTrue(got["found"])
        self.assertEqual(got["action"], "deny")
        self.assertEqual(got["values"]["amount"], 1500)
        self.assertEqual(got["values"]["country"], "RU")
        self.assertEqual(len(got["replay"]), 1)
        self.assertFalse(got["replay"][0]["changed"])

    def test_blocked_event_keeps_full_values(self):
        evaluate({"id": "e9", "amount": 5000, "device": "ios", "ip": "1.2.3.4"})
        got = explain("e9")
        self.assertEqual(got["values"]["ip"], "1.2.3.4")
        self.assertEqual(got["values"]["device"], "ios")

    def test_explain_unknown_event(self):
        self.assertFalse(explain("nope")["found"])


if __name__ == "__main__":
    unittest.main()
