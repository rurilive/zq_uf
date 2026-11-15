"""规则优先级测试：priority 仲裁、注册顺序兜底、路径匹配、幂等。"""

import unittest

from linkrewrite import Registry, Rule, rewrite, rules


def registry_with(site, *rule_dicts):
    registry = Registry()
    for data in rule_dicts:
        registry.add_rule(site, Rule(**data))
    return registry


class PriorityTest(unittest.TestCase):
    def test_higher_priority_delete_beats_lower_inject(self):
        registry = registry_with(
            "s",
            dict(
                id="hi",
                priority=100,
                tracking={"delete": ["utm_source"]},
            ),
            dict(
                id="lo",
                priority=10,
                tracking={"inject": {"utm_source": "mail"}},
            ),
        )
        result = rewrite("https://s/p?utm_source=ad", "s",
                         registry=registry)
        self.assertNotIn("utm_source", result["after"])
        self.assertEqual(
            result["conflicts"][0]["winner"]["rule"], "hi"
        )

    def test_higher_priority_inject_beats_lower_delete(self):
        registry = registry_with(
            "s",
            dict(
                id="hi-inject",
                priority=100,
                tracking={"inject": {"utm_source": "mail"}},
            ),
            dict(
                id="lo-delete",
                priority=10,
                tracking={"delete": ["utm_source"]},
            ),
        )
        result = rewrite("https://s/p?utm_source=ad", "s",
                         registry=registry)
        # 高优先级注入覆盖原值，低优先级删除被忽略
        self.assertIn("utm_source=mail", result["after"])
        self.assertEqual(
            result["conflicts"][0]["winner"]["rule"], "hi-inject"
        )

    def test_equal_priority_resolved_by_registration_order(self):
        registry = registry_with(
            "s",
            dict(id="first", priority=50,
                 tracking={"delete": ["spm"]}),
            dict(id="second", priority=50,
                 tracking={"inject": {"spm": "x"}}),
        )
        result = rewrite("https://s/p?spm=abc", "s", registry=registry)
        self.assertNotIn("spm", result["after"])
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["winner"]["rule"], "first")
        self.assertIn("注册先后", conflict["reason"])

    def test_rules_listed_in_priority_order(self):
        registry = registry_with(
            "s",
            dict(id="low", priority=1),
            dict(id="high", priority=99),
            dict(id="mid", priority=50),
        )
        ids = [item["id"] for item in rules("s", registry=registry)["rules"]]
        self.assertEqual(ids, ["high", "mid", "low"])

    def test_path_scoped_rules_only_match_own_paths(self):
        registry = registry_with(
            "s",
            dict(id="article", priority=50, path=r"^/article/",
                 tracking={"delete": ["utm_source"]}),
            dict(id="section", priority=50, path=r"^/section/",
                 other={"retain": ["sort"]}),
        )
        article = rewrite(
            "https://s/article/1?utm_source=x&q=1", "s", registry=registry
        )
        self.assertEqual([h["rule"] for h in article["hit"]], ["article"])
        self.assertNotIn("utm_source", article["after"])

        section = rewrite(
            "https://s/section/t?sort=hot&junk=2", "s", registry=registry
        )
        self.assertEqual([h["rule"] for h in section["hit"]], ["section"])
        self.assertIn("sort=hot", section["after"])
        self.assertNotIn("junk", section["after"])

    def test_path_exclusive_rules_have_no_static_conflict(self):
        registry = registry_with(
            "s",
            dict(id="a", priority=50, path=r"^/article/",
                 pagination={"retain": ["page"]}),
            dict(id="b", priority=50, path=r"^/section/",
                 pagination={"retain": ["page"]}),
        )
        self.assertEqual(
            rules("s", registry=registry)["conflicts"], []
        )

    def test_retain_whitelist_drops_unmentioned_params(self):
        registry = registry_with(
            "s",
            dict(id="w", priority=10, pagination={"retain": ["page"]}),
        )
        result = rewrite(
            "https://s/p?page=2&utm_source=x&junk=1", "s",
            registry=registry,
        )
        self.assertIn("page=2", result["after"])
        self.assertNotIn("utm_source", result["after"])
        self.assertNotIn("junk", result["after"])

    def test_without_retain_unmentioned_params_kept(self):
        registry = registry_with(
            "s",
            dict(id="d", priority=10, tracking={"delete": ["utm_source"]}),
        )
        result = rewrite(
            "https://s/p?utm_source=x&junk=1&page=2", "s",
            registry=registry,
        )
        self.assertIn("junk=1", result["after"])
        self.assertIn("page=2", result["after"])

    def test_idempotent_rewrite(self):
        registry = registry_with(
            "s",
            dict(id="strip", priority=100,
                 tracking={"delete": ["utm_source", "spm"]}),
            dict(id="page", priority=50,
                 pagination={"retain": ["page"],
                             "inject": {"page_size": "20"}}),
        )
        url = "https://s/p?utm_source=a&spm=b&page=3&keep=1"
        first = rewrite(url, "s", registry=registry)
        second = rewrite(first["after"], "s", registry=registry)
        third = rewrite(second["after"], "s", registry=registry)
        self.assertEqual(first["after"], second["after"])
        self.assertEqual(second["after"], third["after"])
        # 第二次起不再产生实际变更
        self.assertTrue(
            all(
                not second["changes"][cls]["removed"]
                and not second["changes"][cls]["injected"]
                and not second["changes"][cls]["changed"]
                for cls in second["changes"]
            )
        )

    def test_deterministic_query_order(self):
        registry = registry_with(
            "s",
            dict(id="i", priority=10,
                 pagination={"inject": {"page_size": "20"}}),
        )
        url = "https://s/p?page=2&z=1&a=2"
        first = rewrite(url, "s", registry=registry)["after"]
        second = rewrite(url, "s", registry=registry)["after"]
        self.assertEqual(first, second)
        # 原参数顺序保留，新注入参数固定追加在末尾
        self.assertTrue(first.endswith("&page_size=20"))


if __name__ == "__main__":
    unittest.main()
