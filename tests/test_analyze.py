import unittest

from sandboxreport import (
    ReportParseError,
    merge_samples,
    parse,
    parse_batch,
    reset,
    static_mismatches,
    summary,
    types,
    unparsed,
)

REPORT = {
    "id": "s-001",
    "behaviors": [
        {"type": "network", "action": "connect 10.0.0.5:443", "seconds": 12},
        {"type": "file", "action": "write /tmp/a.tmp", "seconds": 3},
        {"type": "network", "action": "connect 10.0.0.5:443", "seconds": 9},
    ],
    "static": {"packer": "upx", "entropy": 7.4},
}

SECTION_REPORT = {
    "id": "s-002",
    "sections": {
        "network": [
            {"action": "connect 1.2.3.4:4444", "time": "00:05", "suspicious": True},
            {"action": "dns query evil.xyz", "seconds": 20},
        ],
        "file": [
            {"action": "encrypt /data/1.doc", "seconds": 7},
        ],
        "process": [],
        "registry": [
            {"action": "set HKCU\\Run\\updater", "seconds": 11},
        ],
    },
    "static": {
        "packer": "upx",
        "capabilities": ["socket", "CreateFileA", "RegSetValueExW"],
    },
}

TEXT_REPORT = """样本 ID: s-003

[网络行为]
[00:12] connect 10.0.0.5:443
[00:09] connect 10.0.0.5:443
[00:15] dns query bad.top [异常]

[文件行为]
[00:03] write /tmp/a.tmp

[进程行为]
[00:06] inject explorer.exe [可疑]

[静态特征]
packer=upx
entropy=7.4
capabilities=socket, CreateFileA
"""


class ParseTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_repeated_behavior_merged_with_count(self):
        out = parse(REPORT)
        self.assertEqual(len(out["behaviors"]), 2)
        net = next(b for b in out["behaviors"] if b["type"] == "network")
        self.assertEqual(net["count"], 2)
        self.assertEqual(net["seconds"], [12, 9])
        self.assertEqual(net["first_seconds"], 9)
        self.assertEqual(net["last_seconds"], 12)

    def test_behaviors_sorted_by_first_occurrence(self):
        out = parse(REPORT)
        self.assertEqual(
            [b["first_seconds"] for b in out["behaviors"]], [3, 9]
        )

    def test_key_behavior_flagged_with_seconds(self):
        out = parse(REPORT)
        net = next(b for b in out["behaviors"] if b["type"] == "network")
        self.assertTrue(net["key"])
        self.assertEqual(net["first_seconds"], 9)

    def test_static_features_returned_alongside_behaviors(self):
        out = parse(REPORT)
        self.assertEqual(out["static"], {"packer": "upx", "entropy": 7.4})
        self.assertIn("anomalies", out)

    def test_anomaly_rules_flag_suspicious_actions(self):
        out = parse(SECTION_REPORT)
        rules = {rule for item in out["anomalies"] for rule in item["rules"]}
        self.assertIn("ANM_NET_C2_PORT", rules)
        self.assertIn("ANM_NET_SUSPICIOUS_DNS", rules)
        self.assertIn("ANM_FILE_RANSOM", rules)
        self.assertIn("ANM_REG_RUN", rules)
        c2 = next(
            item
            for item in out["anomalies"]
            if "ANM_NET_C2_PORT" in item["rules"]
        )
        self.assertEqual(c2["first_seconds"], 5)

    def test_report_flagged_suspicious_entry(self):
        out = parse(SECTION_REPORT)
        c2 = next(
            b
            for b in out["behaviors"]
            if b["action"] == "connect 1.2.3.4:4444"
        )
        self.assertTrue(c2["anomaly"])
        self.assertIn("REPORT_FLAGGED", c2["anomaly_rules"])

    def test_text_format_parsed_and_merged(self):
        out = parse(TEXT_REPORT)
        self.assertEqual(out["format"], "text")
        self.assertEqual(out["id"], "s-003")
        net = next(
            b for b in out["behaviors"] if b["action"] == "connect 10.0.0.5:443"
        )
        self.assertEqual(net["count"], 2)
        # 00:09 早于 00:12，按首次出现排序。
        self.assertEqual(net["first_seconds"], 9)
        self.assertEqual(
            [b["first_seconds"] for b in out["behaviors"]],
            [3, 6, 9, 15],
        )

    def test_text_suspicious_tag_stripped_but_flagged(self):
        out = parse(TEXT_REPORT)
        dns = next(
            b for b in out["behaviors"] if b["action"] == "dns query bad.top"
        )
        self.assertTrue(dns["anomaly"])
        self.assertIn("REPORT_FLAGGED", dns["anomaly_rules"])
        inject = next(b for b in out["behaviors"] if "inject" in b["action"])
        self.assertIn("ANM_PROC_INJECT", inject["anomaly_rules"])

    def test_text_static_values_typed(self):
        out = parse(TEXT_REPORT)
        self.assertEqual(out["static"]["packer"], "upx")
        self.assertEqual(out["static"]["entropy"], 7.4)
        self.assertEqual(
            out["static"]["capabilities"], ["socket", "CreateFileA"]
        )

    def test_unknown_format_preserves_report_and_raises(self):
        weird = {"id": "s-009", "sections_typo": []}
        with self.assertRaises(ReportParseError) as ctx:
            parse(weird)
        self.assertIs(ctx.exception.report, weird)
        self.assertEqual(unparsed(), [])  # 单 parse 失败不留档，batch 才留档

    def test_unknown_text_section_raises_with_original(self):
        bad = "样本 ID: s-010\n[奇怪的分节]\n[00:01] x"
        with self.assertRaises(ReportParseError) as ctx:
            parse(bad)
        self.assertEqual(ctx.exception.report, bad)

    def test_missing_timestamp_line_raises(self):
        bad = "样本 ID: s-011\n[网络行为]\nconnect 1.1.1.1:80"
        with self.assertRaises(ReportParseError):
            parse(bad)

    def test_unknown_behavior_type_flagged_as_anomaly(self):
        report = {
            "id": "s-012",
            "behaviors": [
                {"type": "clipboard", "action": "read clipboard", "seconds": 2}
            ],
            "static": {},
        }
        out = parse(report)
        item = out["behaviors"][0]
        self.assertTrue(item["anomaly"])
        self.assertIn("UNKNOWN_BEHAVIOR_TYPE", item["anomaly_rules"])

    def test_static_mismatch_declared_but_absent(self):
        report = {
            "id": "s-013",
            "behaviors": [
                {"type": "file", "action": "write /tmp/x", "seconds": 1}
            ],
            "static": {"apis": ["socket", "connect"]},
        }
        out = parse(report)
        mismatch = out["static_mismatches"][0]
        self.assertEqual(mismatch["type"], "network")
        self.assertEqual(mismatch["direction"], "declared_but_absent")

    def test_static_mismatch_observed_but_undeclared(self):
        report = {
            "id": "s-014",
            "behaviors": [
                {"type": "registry", "action": "set HKCU\\Run\\x", "seconds": 1}
            ],
            "static": {"apis": ["CreateFileA"]},
        }
        out = parse(report)
        directions = {m["type"]: m["direction"] for m in out["static_mismatches"]}
        self.assertEqual(directions["file"], "declared_but_absent")
        self.assertEqual(directions["registry"], "observed_but_undeclared")

    def test_no_mismatch_when_static_has_no_hints(self):
        out = parse(REPORT)
        self.assertEqual(out["static_mismatches"], [])
        self.assertEqual(static_mismatches(), [])

    def test_missing_seconds_sorts_last_but_kept(self):
        report = {
            "id": "s-015",
            "behaviors": [
                {"type": "file", "action": "write /no-time", "seconds": None},
                {"type": "file", "action": "write /early", "seconds": 2},
            ],
            "static": {},
        }
        out = parse(report)
        self.assertEqual(
            [b["action"] for b in out["behaviors"]],
            ["write /early", "write /no-time"],
        )
        self.assertIsNone(out["behaviors"][-1]["first_seconds"])

    def test_iso_clock_time_normalized_to_seconds(self):
        report = {
            "id": "s-016",
            "behaviors": [
                {"type": "network", "action": "connect h:80", "seconds": "01:02:03"}
            ],
            "static": {},
        }
        out = parse(report)
        self.assertEqual(out["behaviors"][0]["first_seconds"], 3723)

    def test_empty_behaviors_yields_empty_summary(self):
        out = parse({"id": "s-017", "behaviors": [], "static": {}})
        self.assertEqual(out["behaviors"], [])
        self.assertEqual(summary("s-017")["total"], 0)
        self.assertEqual(types()["types"], [])


class SummaryTest(unittest.TestCase):
    def setUp(self):
        reset()
        parse(REPORT)
        parse(SECTION_REPORT)

    def test_summary_counts_occurrences(self):
        out = summary("s-001")
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["unique"], 2)
        self.assertEqual(out["by_type"], {"network": 2, "file": 1})

    def test_summary_filter_by_type(self):
        out = summary("s-001", behavior_type="network")
        self.assertEqual(out["total"], 2)
        self.assertEqual(len(out["behaviors"]), 1)
        self.assertEqual(out["by_type"], {"network": 2})

    def test_summary_invalid_type_raises(self):
        with self.assertRaises(ValueError):
            summary("s-001", behavior_type="clipboard")

    def test_summary_unknown_sample_raises(self):
        with self.assertRaises(ValueError):
            summary("nope")

    def test_summary_carries_static_and_anomalies(self):
        out = summary("s-002")
        self.assertEqual(out["static"]["packer"], "upx")
        self.assertTrue(out["anomalies"])
        only_net = summary("s-002", behavior_type="network")
        self.assertTrue(
            all(a["type"] == "network" for a in only_net["anomalies"])
        )

    def test_types_lists_observed_in_fixed_order(self):
        self.assertEqual(types()["types"], ["network", "file", "registry"])


class BatchTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_batch_partial_failure_keeps_originals(self):
        bad = {"id": "s-bad", "sections": []}
        out = parse_batch([REPORT, bad, TEXT_REPORT])
        self.assertEqual([r["id"] for r in out["parsed"]], ["s-001", "s-003"])
        self.assertEqual(len(out["failed"]), 1)
        self.assertIs(out["failed"][0]["report"], bad)
        self.assertTrue(out["failed"][0]["error"])
        self.assertEqual(unparsed()[0]["report"], bad)

    def test_merge_samples_aggregates_across_reports(self):
        parse(REPORT)
        parse(TEXT_REPORT)
        out = merge_samples()
        connect = next(
            b
            for b in out["behaviors"]
            if b["action"] == "connect 10.0.0.5:443"
        )
        # 两份报告各出现 2 次。
        self.assertEqual(connect["count"], 4)
        self.assertEqual(
            {s["id"]: s["count"] for s in connect["samples"]},
            {"s-001": 2, "s-003": 2},
        )
        self.assertEqual(out["sample_ids"], ["s-001", "s-003"])

    def test_merge_specific_samples_with_type_filter(self):
        parse(REPORT)
        parse(TEXT_REPORT)
        out = merge_samples(["s-003"], behavior_type="network")
        self.assertTrue(all(b["type"] == "network" for b in out["behaviors"]))
        self.assertEqual(out["by_type"], {"network": 3})

    def test_merge_unknown_sample_raises(self):
        with self.assertRaises(ValueError):
            merge_samples(["ghost"])

    def test_static_mismatch_report_lists_samples(self):
        parse(
            {
                "id": "s-m",
                "behaviors": [
                    {"type": "file", "action": "write x", "seconds": 1}
                ],
                "static": {"apis": ["socket"]},
            }
        )
        listed = static_mismatches()
        self.assertEqual([row["id"] for row in listed], ["s-m"])
        self.assertEqual(listed[0]["mismatches"][0]["type"], "network")

    def test_batch_all_failed_leaves_no_reports(self):
        out = parse_batch(["随便一段没有分节的文本", 42])
        self.assertEqual(out["parsed"], [])
        self.assertEqual(len(out["failed"]), 2)
        self.assertEqual(len(unparsed()), 2)

    def test_merge_sorted_by_earliest_seconds(self):
        parse(
            {
                "id": "s-a",
                "behaviors": [
                    {"type": "file", "action": "write late", "seconds": 30},
                    {"type": "file", "action": "write early", "seconds": 4},
                ],
                "static": {},
            }
        )
        out = merge_samples()
        self.assertEqual(
            [b["action"] for b in out["behaviors"]],
            ["write early", "write late"],
        )

    def test_reset_clears_everything(self):
        parse(REPORT)
        reset()
        with self.assertRaises(ValueError):
            summary("s-001")
        self.assertEqual(unparsed(), [])


if __name__ == "__main__":
    unittest.main()
