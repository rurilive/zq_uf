"""冲突测试：静态/运行时冲突解释、跨站点规则隔离、回滚。"""

import unittest

from linkrewrite import Registry, Rule, rewrite, rules


class ConflictTest(unittest.TestCase):
    def _registry(self):
        registry = Registry()
        registry.add_rule(
            "s",
            Rule(
                id="r-high",
                priority=100,
                tracking={"delete": ["utm_source"]},
            ),
        )
        registry.add_rule(
            "s",
            Rule(
                id="r-low",
                priority=10,
                tracking={"inject": {"utm_source": "mail"}},
            ),
        )
        return registry

    def test_static_conflict_explained(self):
        registry = self._registry()
        conflicts = rules("s", registry=registry)["conflicts"]
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict["class"], "tracking")
        self.assertEqual(conflict["param"], "utm_source")
        self.assertEqual(conflict["winner"]["rule"], "r-high")
        self.assertEqual(conflict["winner"]["priority"], 100)
        self.assertEqual(conflict["losers"][0]["rule"], "r-low")
        self.assertIn("优先级", conflict["reason"])
        self.assertIn("r-high", conflict["reason"])

    def test_runtime_conflict_only_for_relevant_params(self):
        registry = self._registry()
        # URL 不含竞争参数时，运行结果里不重复报冲突，静态规则里仍然可见
        result = rewrite("https://s/p?page=2", "s", registry=registry)
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(
            len(rules("s", registry=registry)["conflicts"]), 1
        )

        hit = rewrite(
            "https://s/p?utm_source=ad", "s", registry=registry
        )
        self.assertEqual(len(hit["conflicts"]), 1)
        self.assertNotIn("utm_source", hit["after"])

    def test_same_rule_internal_contradiction_rejected(self):
        with self.assertRaises(ValueError):
            Rule(
                id="bad",
                tracking={
                    "retain": ["utm_source"],
                    "delete": ["utm_source"],
                },
            )
        with self.assertRaises(ValueError):
            Rule(
                id="bad2",
                tracking={
                    "retain": ["utm_source"],
                    "inject": {"utm_source": "x"},
                },
            )

    def test_duplicate_param_in_one_section_rejected(self):
        with self.assertRaises(ValueError):
            Rule(id="dup", tracking={"delete": ["spm", "spm"]})

    def test_sites_are_isolated(self):
        registry = Registry()
        registry.add_rule(
            "site-a",
            Rule(id="a-strip", priority=10,
                 tracking={"delete": ["utm_source"]}),
        )
        registry.add_rule(
            "site-b",
            Rule(id="b-inject", priority=10,
                 tracking={"inject": {"utm_source": "b"}}),
        )
        # 同参数在不同站点各自生效，互不构成冲突
        self.assertEqual(
            rules("site-a", registry=registry)["conflicts"], []
        )
        self.assertEqual(
            rules("site-b", registry=registry)["conflicts"], []
        )
        a = rewrite("https://a/p?utm_source=x", "site-a",
                    registry=registry)
        b = rewrite("https://b/p?utm_source=x", "site-b",
                    registry=registry)
        self.assertNotIn("utm_source", a["after"])
        self.assertIn("utm_source=b", b["after"])

    def test_unknown_site_passthrough(self):
        registry = Registry()
        result = rewrite(
            "https://x/p?utm_source=1&page=9", "nope", registry=registry
        )
        self.assertEqual(result["after"], result["before"])
        self.assertEqual(result["hit"], [])
        self.assertFalse(result["matched"])


class RollbackTest(unittest.TestCase):
    def test_rollback_restores_previous_rule_set(self):
        registry = Registry()
        registry.add_rule(
            "s", Rule(id="r1", priority=10,
                      tracking={"delete": ["utm_source"]})
        )
        rev1 = registry.current_revision("s")
        registry.add_rule(
            "s", Rule(id="r2", priority=20,
                      pagination={"retain": ["page"]})
        )
        self.assertEqual(
            [r["id"] for r in rules("s", registry=registry)["rules"]],
            ["r2", "r1"],
        )

        new_rev = registry.rollback("s", rev1)
        ids = [r["id"] for r in rules("s", registry=registry)["rules"]]
        self.assertEqual(ids, ["r1"])
        # 回滚本身产生新版本，历史完整保留
        self.assertEqual(registry.current_revision("s"), new_rev)
        self.assertIn(rev1, registry.revisions("s"))

        # 回滚后的行为也随之恢复
        result = rewrite(
            "https://s/p?utm_source=x&page=2&junk=1", "s",
            registry=registry,
        )
        self.assertNotIn("utm_source", result["after"])
        self.assertIn("page=2", result["after"])
        self.assertIn("junk=1", result["after"])

    def test_rollback_to_zero_clears_site(self):
        registry = Registry()
        registry.add_rule(
            "s", Rule(id="r1", tracking={"delete": ["utm_source"]})
        )
        registry.rollback("s", 0)
        self.assertEqual(rules("s", registry=registry)["rules"], [])
        result = rewrite(
            "https://s/p?utm_source=x", "s", registry=registry
        )
        self.assertEqual(result["after"], result["before"])

    def test_rollback_unknown_revision_raises(self):
        registry = Registry()
        with self.assertRaises(ValueError):
            registry.rollback("s", 99)

    def test_duplicate_rule_id_rejected(self):
        registry = Registry()
        registry.add_rule("s", Rule(id="r1"))
        with self.assertRaises(ValueError):
            registry.add_rule("s", Rule(id="r1"))

    def test_remove_rule_is_versioned(self):
        registry = Registry()
        registry.add_rule("s", Rule(id="r1"))
        rev = registry.current_revision("s")
        registry.remove_rule("s", "r1")
        self.assertEqual(rules("s", registry=registry)["rules"], [])
        registry.rollback("s", rev)
        self.assertEqual(
            [r["id"] for r in rules("s", registry=registry)["rules"]],
            ["r1"],
        )


if __name__ == "__main__":
    unittest.main()
