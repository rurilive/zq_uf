import unittest

from robotsdiff import (
    affected,
    allowed,
    audit,
    cache,
    diff,
    rollback,
    update,
)
from robotsdiff import robots


class RobotsDiffTest(unittest.TestCase):
    def setUp(self):
        robots._reset()

    def tearDown(self):
        robots._reset()

    # ---- 差异分类 ----

    def test_diff_newly_and_lifted_disallowed(self):
        update("shop", {"disallow": ["/old", "/keep"]})
        update("shop", {"disallow": ["/keep", "/new"]})
        result = diff("shop")
        self.assertEqual(result["newly_disallowed"], ["/new"])
        self.assertEqual(result["lifted_disallowed"], ["/old"])
        self.assertEqual(result["newly_allowed"], [])
        self.assertEqual(result["removed_allow"], [])

    def test_diff_allow_segments(self):
        update("shop", {"allow": ["/a"], "disallow": ["/"]})
        update("shop", {"allow": ["/b"], "disallow": ["/"]})
        result = diff("shop")
        self.assertEqual(result["newly_allowed"], ["/b"])
        self.assertEqual(result["removed_allow"], ["/a"])
        self.assertEqual(result["newly_disallowed"], [])
        self.assertEqual(result["lifted_disallowed"], [])

    def test_diff_identical_rules_is_empty(self):
        update("shop", {"disallow": ["/p"]})
        update("shop", {"disallow": ["/p"]})
        result = diff("shop")
        for key in result:
            self.assertEqual(result[key], [])

    def test_diff_unknown_host_is_empty(self):
        result = diff("ghost")
        self.assertEqual(set(result), set(robots._EMPTY_DIFF))
        for value in result.values():
            self.assertEqual(value, [])

    # ---- 定向失效 ----

    def test_only_affected_paths_invalidated(self):
        update("shop", {"disallow": ["/private"]})
        # 预热缓存：三条链接各判定一次
        self.assertFalse(allowed("shop", "/private/1"))
        self.assertTrue(allowed("shop", "/public/1"))
        self.assertTrue(allowed("shop", "/public/2"))

        update("shop", {"disallow": ["/private", "/blocked"]})
        hit = affected("shop")
        # /blocked 下没有缓存链接，因此没有任何已缓存路径判定翻转
        self.assertEqual(hit, [])

        # 新路径按新规则判定并被缓存
        self.assertFalse(allowed("shop", "/blocked/9"))
        self.assertEqual(
            cache("shop"),
            {
                "/private/1": False,
                "/public/1": True,
                "/public/2": True,
                "/blocked/9": False,
            },
        )

    def test_targeted_invalidation_drops_only_flipped(self):
        update("shop", {"disallow": ["/private"]})
        self.assertTrue(allowed("shop", "/new/1"))
        self.assertFalse(allowed("shop", "/private/1"))
        self.assertTrue(allowed("shop", "/public/1"))

        update("shop", {"disallow": ["/private", "/new"]})
        self.assertEqual(affected("shop"), ["/new/1"])
        self.assertNotIn("/new/1", cache("shop"))
        # 未受影响的缓存原样保留
        self.assertEqual(cache("shop")["/public/1"], True)
        self.assertEqual(cache("shop")["/private/1"], False)
        # 重新访问按新规则判定
        self.assertFalse(allowed("shop", "/new/1"))

    def test_lift_disallow_flips_and_invalidates(self):
        update("shop", {"disallow": ["/private"]})
        self.assertFalse(allowed("shop", "/private/1"))
        update("shop", {"disallow": []})
        self.assertEqual(affected("shop"), ["/private/1"])
        self.assertTrue(allowed("shop", "/private/1"))

    def test_allow_takes_precedence(self):
        update(
            "shop",
            {"allow": ["/pub/open"], "disallow": ["/pub"]},
        )
        self.assertTrue(allowed("shop", "/pub/open/1"))
        self.assertFalse(allowed("shop", "/pub/closed"))

    # ---- 审计 ----

    def test_audit_records_time_source_and_summary(self):
        update(
            "shop",
            {"disallow": ["/p"]},
            source="https://shop.example/robots.txt",
            fetched_at="2026-11-15T08:00:00+00:00",
        )
        records = audit("shop")
        self.assertEqual(len(records), 1)
        entry = records[0]
        self.assertEqual(entry["action"], "update")
        self.assertEqual(entry["source"], "https://shop.example/robots.txt")
        self.assertEqual(entry["fetched_at"], "2026-11-15T08:00:00+00:00")
        self.assertIn("time", entry)
        self.assertIn("sha256", entry["summary"])
        self.assertIn("disallow=1", entry["summary"])
        self.assertEqual(entry["diff"]["newly_disallowed"], ["/p"])
        self.assertEqual(entry["invalidation"]["invalidated"], 0)

    def test_audit_source_dict_and_defaults(self):
        update(
            "shop",
            {"disallow": ["/p"]},
            source={
                "url": "https://shop.example/robots.txt",
                "fetched_at": "2026-11-15T09:00:00+00:00",
            },
        )
        entry = audit("shop")[0]
        self.assertEqual(entry["source"], "https://shop.example/robots.txt")
        self.assertEqual(entry["fetched_at"], "2026-11-15T09:00:00+00:00")
        self.assertIn("sha256", entry["summary"])

    def test_audit_is_append_only_and_contains_invalidation(self):
        update("shop", {"disallow": ["/p"]})
        self.assertFalse(allowed("shop", "/p/1"))  # 缓存 False
        update("shop", {"disallow": ["/q"]})
        records = audit("shop")
        self.assertEqual([r["action"] for r in records], ["update", "update"])
        self.assertEqual(records[1]["diff"]["lifted_disallowed"], ["/p"])
        self.assertEqual(records[1]["diff"]["newly_disallowed"], ["/q"])
        self.assertEqual(records[1]["invalidation"]["affected"], ["/p/1"])
        self.assertEqual(records[1]["invalidation"]["invalidated"], 1)

    def test_audit_unknown_host_is_empty(self):
        self.assertEqual(audit("ghost"), [])

    # ---- 回滚 ----

    def test_rollback_restores_previous_rules(self):
        update("shop", {"disallow": ["/p"]})
        update("shop", {"disallow": ["/q"]})
        rollback("shop")
        self.assertFalse(allowed("shop", "/p/x"))
        self.assertTrue(allowed("shop", "/q/x"))
        entries = audit("shop")
        self.assertEqual(entries[-1]["action"], "rollback")
        self.assertEqual(entries[-1]["rollback_to"], 1)
        self.assertEqual(entries[-1]["source"], entries[0]["source"])

    def test_rollback_first_update_returns_to_open_default(self):
        update("shop", {"disallow": ["/p"]}, source="u")
        self.assertFalse(allowed("shop", "/p/1"))
        rollback("shop")
        self.assertTrue(allowed("shop", "/p/1"))
        entry = audit("shop")[-1]
        self.assertEqual(entry["action"], "rollback")
        self.assertIsNone(entry["rollback_to"])
        self.assertEqual(entry["source"], "u")
        self.assertIn("disallow=0", entry["summary"])

    def test_rollback_without_history_raises(self):
        with self.assertRaises(KeyError):
            rollback("ghost")

    def test_rollback_invalidates_affected_cache(self):
        update("shop", {"disallow": ["/p"]})
        self.assertFalse(allowed("shop", "/p/1"))
        update("shop", {"disallow": []})
        self.assertTrue(allowed("shop", "/p/1"))
        rollback("shop")
        # 翻转路径被定向失效，重新访问按回滚后的旧规则判定
        self.assertNotIn("/p/1", cache("shop"))
        self.assertFalse(allowed("shop", "/p/1"))
        self.assertEqual(
            audit("shop")[-1]["invalidation"]["affected"], ["/p/1"]
        )

    def test_rollback_allow_rule_flip(self):
        update("shop", {"allow": ["/a"], "disallow": ["/"]})
        self.assertTrue(allowed("shop", "/a/1"))
        update("shop", {"disallow": ["/"]})
        self.assertFalse(allowed("shop", "/a/1"))
        rollback("shop")
        self.assertTrue(allowed("shop", "/a/1"))


if __name__ == "__main__":
    unittest.main()
