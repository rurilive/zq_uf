"""边界场景：无 query、重复参数、白名单注入、片段与空值保留。"""

import unittest

from linkrewrite import Registry, Rule, rewrite


class EdgeCaseTest(unittest.TestCase):
    def _registry(self, *rules, site="s"):
        registry = Registry()
        for rule in rules:
            registry.add_rule(site, rule)
        return registry

    def test_inject_under_whitelist_mode(self):
        registry = self._registry(
            Rule(
                id="w",
                priority=10,
                pagination={
                    "retain": ["page"],
                    "inject": {"page_size": "20"},
                },
            )
        )
        result = rewrite("https://s/p?junk=1", "s", registry=registry)
        self.assertEqual(result["after"], "https://s/p?page_size=20")
        self.assertIn("junk", result["changes"]["other"]["removed"])

    def test_url_without_query_gets_injected(self):
        registry = self._registry(
            Rule(id="i", priority=10,
                 pagination={"inject": {"page_size": "20"}})
        )
        result = rewrite("https://s/p", "s", registry=registry)
        self.assertEqual(result["after"], "https://s/p?page_size=20")
        # 再次重写保持幂等
        again = rewrite(result["after"], "s", registry=registry)
        self.assertEqual(again["after"], result["after"])

    def test_duplicate_params_keep_first_value(self):
        registry = self._registry(
            Rule(id="d", priority=10, other={"delete": ["x"]})
        )
        result = rewrite("https://s/p?x=1&x=2&y=3", "s",
                         registry=registry)
        self.assertEqual(result["after"], "https://s/p?y=3")

    def test_blank_value_params_supported(self):
        registry = self._registry(
            Rule(id="d", priority=10, tracking={"delete": ["utm_source"]})
        )
        result = rewrite("https://s/p?utm_source=&q=1", "s",
                         registry=registry)
        self.assertEqual(result["after"], "https://s/p?q=1")

    def test_fragment_is_preserved(self):
        registry = self._registry(
            Rule(id="d", priority=10, tracking={"delete": ["utm_source"]})
        )
        result = rewrite(
            "https://s/p?utm_source=x#section-2", "s", registry=registry
        )
        self.assertEqual(result["after"], "https://s/p#section-2")

    def test_no_change_returns_identical_url_string(self):
        registry = self._registry(
            Rule(id="d", priority=10, tracking={"delete": ["utm_source"]})
        )
        url = "https://s/p?page=2&q=1"
        result = rewrite(url, "s", registry=registry)
        self.assertIs(result["after"], url)

    def test_inject_override_is_idempotent(self):
        registry = self._registry(
            Rule(id="i", priority=10, other={"inject": {"v": "fixed"}})
        )
        first = rewrite("https://s/p?v=old&k=1", "s", registry=registry)
        self.assertEqual(
            first["changes"]["other"]["changed"],
            [{"param": "v", "before": "old", "after": "fixed"}],
        )
        second = rewrite(first["after"], "s", registry=registry)
        self.assertEqual(second["after"], first["after"])
        self.assertEqual(second["changes"]["other"]["changed"], [])

    def test_rewrite_without_any_rules_passthrough(self):
        registry = Registry()
        url = "https://s/p?a=1&b=2"
        result = rewrite(url, "s", registry=registry)
        self.assertEqual(result["after"], url)
        self.assertEqual(result["hit"], [])
        self.assertEqual(result["revision"], 0)


if __name__ == "__main__":
    unittest.main()
