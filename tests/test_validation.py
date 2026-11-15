"""注入参数合法性校验测试：非法规则在注册时即被拒绝。"""

import unittest

from linkrewrite import InvalidRule, Registry, Rule, rule_from_dict


class ValidationTest(unittest.TestCase):
    def test_scalar_inject_accepted(self):
        rule = Rule(id="r", tracking={"inject": {"utm_source": "mail"}})
        self.assertEqual(
            rule.actions["tracking"]["inject"]["utm_source"]["value"],
            "mail",
        )

    def test_numeric_and_bool_stringified(self):
        rule = Rule(
            id="r",
            pagination={
                "inject": {"page_size": 20, "enabled": True}
            },
        )
        inject = rule.actions["pagination"]["inject"]
        self.assertEqual(inject["page_size"]["value"], "20")
        self.assertEqual(inject["enabled"]["value"], "True")

    def test_allowed_list_rejects_out_of_set_value(self):
        with self.assertRaises(InvalidRule):
            Rule(
                id="r",
                tracking={
                    "inject": {
                        "channel": {"value": "evil",
                                    "allowed": ["feed", "app"]}
                    }
                },
            )

    def test_allowed_list_accepts_member(self):
        rule = Rule(
            id="r",
            tracking={
                "inject": {
                    "channel": {"value": "app", "allowed": ["feed", "app"]}
                }
            },
        )
        self.assertEqual(
            rule.actions["tracking"]["inject"]["channel"]["value"], "app"
        )

    def test_pattern_rejects_non_matching_value(self):
        with self.assertRaises(InvalidRule):
            Rule(
                id="r",
                pagination={
                    "inject": {"page": {"value": "abc",
                                        "pattern": r"^\d+$"}}
                },
            )

    def test_bad_pattern_rejected(self):
        with self.assertRaises(InvalidRule):
            Rule(
                id="r",
                pagination={
                    "inject": {"page": {"value": "1",
                                        "pattern": r"(\d"}}
                },
            )

    def test_min_max_bounds_enforced(self):
        ok = Rule(
            id="ok",
            pagination={"inject": {"page_size": {"value": "50",
                                                 "min": 1, "max": 100}}},
        )
        self.assertEqual(
            ok.actions["pagination"]["inject"]["page_size"]["value"], "50"
        )
        with self.assertRaises(InvalidRule):
            Rule(
                id="too-big",
                pagination={"inject": {"page_size": {"value": "101",
                                                     "min": 1, "max": 100}}},
            )
        with self.assertRaises(InvalidRule):
            Rule(
                id="not-int",
                pagination={"inject": {"page_size": {"value": "big",
                                                     "min": 1}}},
            )
        with self.assertRaises(InvalidRule):
            Rule(
                id="bad-range",
                pagination={"inject": {"page_size": {"value": "5",
                                                     "min": 10, "max": 1}}},
            )

    def test_bad_param_name_rejected(self):
        with self.assertRaises(InvalidRule):
            Rule(id="r", tracking={"delete": ["bad name"]})
        with self.assertRaises(InvalidRule):
            Rule(id="r", tracking={"delete": [""]})

    def test_bad_rule_shape_rejected(self):
        with self.assertRaises(InvalidRule):
            Rule(id="")
        with self.assertRaises(InvalidRule):
            Rule(id="r", priority="high")
        with self.assertRaises(InvalidRule):
            Rule(id="r", tracking={"unknown_action": ["x"]})
        with self.assertRaises(InvalidRule):
            Rule(id="r", tracking=["utm_source"])
        with self.assertRaises(InvalidRule):
            Rule(id="r", path="([0-9")
        with self.assertRaises(InvalidRule):
            Rule(id="r", tracking={"inject": {"x": object()}})
        with self.assertRaises(InvalidRule):
            Rule(id="r", tracking={"inject": {"x": {"value": ["a", "b"]}}})

    def test_invalid_rule_not_registered(self):
        registry = Registry()
        before = registry.revisions("s")
        with self.assertRaises(InvalidRule):
            registry.add_rule(
                "s",
                Rule(id="bad",
                     pagination={"inject": {"p": {"value": "9", "max": 5}}}),
            )
        self.assertEqual(registry.get_rules("s"), [])
        # 注册失败不产生修订版本
        self.assertEqual(registry.revisions("s"), before)

    def test_rule_from_dict_validates(self):
        rule = rule_from_dict(
            {
                "id": "dict-rule",
                "priority": 30,
                "path": r"^/p/",
                "tracking": {"delete": ["utm_source"]},
            }
        )
        self.assertEqual(rule.id, "dict-rule")
        self.assertEqual(rule.priority, 30)
        with self.assertRaises(InvalidRule):
            rule_from_dict({"priority": 1})


if __name__ == "__main__":
    unittest.main()
