"""参数分类测试：追踪 / 分页 / 普通参数隔离，站点扩展生效。"""

import unittest

from linkrewrite import Registry, Rule, rewrite
from linkrewrite.classifier import (
    OTHER,
    PAGINATION,
    TRACKING,
    classify_param,
)


class ClassifyTest(unittest.TestCase):
    def test_utm_always_tracking(self):
        self.assertEqual(classify_param("utm_custom"), TRACKING)
        self.assertEqual(classify_param("utm_source"), TRACKING)

    def test_known_tracking_params(self):
        for name in ("gclid", "fbclid", "spm", "mc_eid"):
            self.assertEqual(classify_param(name), TRACKING, name)

    def test_known_pagination_params(self):
        for name in ("page", "offset", "limit", "cursor", "page_size"):
            self.assertEqual(classify_param(name), PAGINATION, name)

    def test_other_params(self):
        for name in ("sort", "q", "id", "ref"):
            self.assertEqual(classify_param(name), OTHER, name)

    def test_tracking_takes_precedence_over_pagination(self):
        # 同名同时进两张扩展表时按追踪处理
        self.assertEqual(
            classify_param(
                "marker",
                tracking_extra=["marker"],
                pagination_extra=["marker"],
            ),
            TRACKING,
        )

    def test_site_extra_tracking_param_is_stripped(self):
        registry = Registry()
        registry.configure_site("s1", tracking=["camp_id"])
        registry.add_rule(
            "s1",
            Rule(id="strip", priority=10, tracking={"delete": ["camp_id"]}),
        )
        result = rewrite("https://s1/p?camp_id=9&q=keep", "s1",
                         registry=registry)
        self.assertNotIn("camp_id", result["after"])
        self.assertIn("q=keep", result["after"])
        # 未扩展该参数的站点不受影响
        other = Registry()
        other.add_rule(
            "s2",
            Rule(id="strip", priority=10, tracking={"delete": ["camp_id"]}),
        )
        untouched = rewrite("https://s2/p?camp_id=9", "s2", registry=other)
        self.assertEqual(
            untouched["after"], "https://s2/p?camp_id=9"
        )

    def test_site_extra_pagination_param_governed_by_pagination_rule(self):
        registry = Registry()
        registry.configure_site("s1", pagination=["pgno"])
        registry.add_rule(
            "s1",
            Rule(id="p", priority=10, pagination={"retain": ["pgno"]}),
        )
        result = rewrite(
            "https://s1/p?pgno=2&junk=1", "s1", registry=registry
        )
        self.assertIn("pgno=2", result["after"])
        self.assertNotIn("junk", result["after"])
        self.assertEqual(
            result["changes"]["pagination"]["removed"], {}
        )
        self.assertIn("junk", result["changes"]["other"]["removed"])


if __name__ == "__main__":
    unittest.main()
