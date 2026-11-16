import unittest

from pcapflow import analyze, load, reset
from tests.test_flow import pkt


def mixed_packets():
    rows = [pkt(1, "10.0.0.5", "203.0.113.7", 443)]     # 外联
    # 4 台内网主机：1.1.0 阈值 3 命中扫描，1.0.0 阈值 5 不命中
    for i, host in enumerate(("10.0.2.1", "10.0.2.2", "10.0.2.3", "10.0.2.4")):
        rows.append(pkt(2 + i, "10.0.0.5", host, 22, sport=41000 + i))
    return rows


class IncrementalTest(unittest.TestCase):
    def setUp(self):
        reset()
        load("cap", mixed_packets())

    def test_first_run_executes_all_rules(self):
        out = analyze("cap", rule_version="1.0.0")
        self.assertEqual(
            sorted(out["incremental"]["rerun_rules"]),
            ["abnormal_port", "external_conn", "lateral_scan", "tunnel"],
        )
        self.assertEqual(out["incremental"]["reused_rules"], [])

    def test_version_bump_reruns_only_changed_rules(self):
        analyze("cap", rule_version="1.0.0")
        out = analyze("cap", rule_version="1.1.0")
        # 只有 lateral_scan / tunnel 配置变了；外联和异常端口规则直接复用
        self.assertEqual(
            sorted(out["incremental"]["rerun_rules"]),
            ["lateral_scan", "tunnel"],
        )
        self.assertEqual(
            sorted(out["incremental"]["reused_rules"]),
            ["abnormal_port", "external_conn"],
        )

    def test_rerun_same_version_reuses_everything(self):
        analyze("cap", rule_version="1.1.0")
        out = analyze("cap", rule_version="1.1.0")
        self.assertEqual(out["incremental"]["rerun_rules"], [])
        self.assertEqual(len(out["incremental"]["reused_rules"]), 4)

    def test_reload_same_packets_keeps_cache(self):
        analyze("cap", rule_version="1.1.0")
        info = load("cap", mixed_packets())
        self.assertTrue(info["cached"])
        out = analyze("cap", rule_version="1.1.0")
        self.assertEqual(len(out["incremental"]["reused_rules"]), 4)

    def test_reload_changed_packets_invalidates_cache(self):
        analyze("cap", rule_version="1.1.0")
        changed = mixed_packets() + [pkt(99, "10.0.0.5", "10.0.9.9", 22, sport=42000)]
        info = load("cap", changed)
        self.assertFalse(info["cached"])
        out = analyze("cap", rule_version="1.1.0")
        self.assertEqual(len(out["incremental"]["rerun_rules"]), 4)


class VersionCompareTest(unittest.TestCase):
    def setUp(self):
        reset()
        load("cap", mixed_packets())

    def test_compare_reports_added_hits(self):
        from pcapflow import compare

        diff = compare("cap", "1.0.0", "1.1.0")
        self.assertEqual(diff["summary"]["hits_a"], 1)   # 1.0.0 只有外联
        self.assertGreaterEqual(diff["summary"]["hits_b"], 4)
        added_types = {h["type"] for h in diff["added"]}
        self.assertIn("lateral_scan", added_types)
        self.assertEqual(diff["summary"]["removed"], 0)


if __name__ == "__main__":
    unittest.main()
