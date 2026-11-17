import unittest

from sandboxreport import (
    UnknownReportFormatError,
    batch,
    inconsistent,
    parse,
    reset,
    summary,
    types,
)

FLAT_REPORT = {
    "id": "s-001",
    "behaviors": [
        {"type": "network", "action": "connect 10.0.0.5:443", "seconds": 12},
        {"type": "file", "action": "write /tmp/a.tmp", "seconds": 3},
        {"type": "network", "action": "connect 10.0.0.5:443", "seconds": 9},
        {"type": "process", "action": "inject explorer.exe", "seconds": 20},
    ],
    "static": {"packer": "upx", "entropy": 7.4, "network": True,
               "file_write": False, "persistence": False},
}

SECTIONED_REPORT = {
    "id": "s-002",
    "sections": [
        {
            "title": "Network Activity / 网络",
            "events": [
                {"detail": "connect 8.8.8.8:53", "at": 5},
                {"detail": "dns query evil.example", "at": 6},
            ],
        },
        {
            "title": "File System / 文件",
            "events": [
                {"op": "write C:\\Users\\u\\a.dll", "time": 2},
            ],
        },
        {
            "title": "Registry / 注册表",
            "events": [
                {"operation": "set HKCU\\...\\CurrentVersion\\Run\\x",
                 "timestamp": 7},
            ],
        },
        {"title": "Misc / 其他", "events": [{"detail": "ignored"}]},
    ],
    "static": {"network": True, "file_write": True, "persistence": True},
}

TEXT_REPORT = """# id: s-003
# 文本日志格式
[12] network: connect 10.0.0.5:443
[3] file: write /tmp/a.tmp
[9] network: connect 10.0.0.5:443
[15] process: spawn powershell -enc abc
static: packer=upx
static: entropy=7.4
"""


class ParseTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_flat_repeated_behavior_merged_with_count(self):
        out = parse(FLAT_REPORT)
        self.assertEqual(out["total"]["entries"], 3)
        self.assertEqual(out["total"]["events"], 4)
        net = next(b for b in out["behaviors"]
                    if b["action"] == "connect 10.0.0.5:443")
        self.assertEqual(net["count"], 2)
        self.assertEqual(net["seconds"], [9, 12])
        self.assertEqual(net["type"], "network")

    def test_behaviors_sorted_by_first_occurrence_seconds(self):
        out = parse(FLAT_REPORT)
        self.assertEqual(
            [b["seconds"][0] for b in out["behaviors"]], [3, 9, 20])
    def test_key_behaviors_flagged_with_seconds(self):
        out = parse(FLAT_REPORT)
        for behavior in out["behaviors"]:
            self.assertTrue(behavior["is_key"])
            self.assertTrue(behavior["seconds"])

    def test_anomalies_listed_separately_with_reason(self):
        out = parse(FLAT_REPORT)
        actions = {a["action"]: a["anomaly"] for a in out["anomalies"]}
        self.assertEqual(actions["inject explorer.exe"],
                         "process injection")
        self.assertIn("outbound network connection",
                      actions["connect 10.0.0.5:443"])
        # 文件写入/投放本身也是高信号异常
        self.assertEqual(actions["write /tmp/a.tmp"], "file write/drop")
        self.assertEqual(len(out["anomalies"]), 3)

    def test_static_features_returned_alongside_behaviors(self):
        out = parse(FLAT_REPORT)
        self.assertEqual(out["static"]["packer"], "upx")
        self.assertEqual(out["static"]["entropy"], 7.4)

    def test_sectioned_format_aliases_and_unknown_sections(self):
        out = parse(SECTIONED_REPORT)
        self.assertEqual(out["format"], "sectioned")
        # 3 个认识的节各产生条目，Misc 节被跳过
        self.assertEqual(out["total"]["entries"], 4)
        self.assertEqual(set(out["by_type"]),
                         {"network", "file", "registry"})
        self.assertTrue(all(b["is_key"] for b in out["behaviors"]))

    def test_text_log_format(self):
        out = parse(TEXT_REPORT)
        self.assertEqual(out["format"], "text")
        self.assertEqual(out["id"], "s-003")
        self.assertEqual(out["static"], {"packer": "upx", "entropy": 7.4})
        self.assertEqual(out["total"], {"entries": 3, "events": 4})
        self.assertEqual(out["behaviors"][0]["seconds"], [3])
        powershell = next(b for b in out["behaviors"]
                          if "powershell" in b["action"])
        self.assertEqual(powershell["anomaly"],
                         "spawns shell/script interpreter")

    def test_unknown_format_preserves_raw_and_raises(self):
        raw = {"id": "s-009", "sections": []}
        # sections 是受支持格式（空分节），这里换一个真正不认识的结构
        raw = {"id": "s-009", "payload": "???"}
        with self.assertRaises(UnknownReportFormatError) as ctx:
            parse(raw)
        self.assertIs(ctx.exception.raw, raw)
        self.assertIn("neither 'behaviors' nor 'sections'", str(ctx.exception))

    def test_malformed_text_line_raises_with_raw(self):
        bad = "# id: s-010\nthis line has no structure\n"
        with self.assertRaises(UnknownReportFormatError) as ctx:
            parse(bad)
        self.assertEqual(ctx.exception.raw, bad)


class SummaryAndCrossCheckTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_summary_counts_merged_entries(self):
        parse(FLAT_REPORT)
        out = summary("s-001")
        self.assertEqual(out["total"], {"entries": 3, "events": 4})
        self.assertEqual(out["by_type"]["network"],
                         {"entries": 1, "events": 2})
        self.assertEqual(out["by_type"]["file"], {"entries": 1, "events": 1})

    def test_summary_filter_by_type(self):
        parse(FLAT_REPORT)
        out = summary("s-001", "network")
        self.assertTrue(all(b["type"] == "network" for b in out["behaviors"]))
        self.assertEqual(out["total"], {"entries": 1, "events": 2})
        self.assertEqual(set(out["by_type"]), {"network"})

    def test_invalid_type_filter_raises(self):
        parse(FLAT_REPORT)
        with self.assertRaises(ValueError):
            summary("s-001", "kernel")

    def test_static_behavior_mismatch_listed(self):
        # s-003 声明了 network 但没有声明 file_write/persistence；
        # 关键是用一个明确对不上的样本来验证对账
        report = {
            "id": "s-010",
            "behaviors": [
                {"type": "file", "action": "write /tmp/x", "seconds": 1},
            ],
            "static": {"network": True, "file_write": True},
        }
        out = parse(report)
        self.assertFalse(out["consistent"])
        mismatch = out["mismatches"][0]
        self.assertEqual(mismatch["feature"], "network")
        self.assertTrue(mismatch["declared"])
        self.assertFalse(mismatch["observed"])
        self.assertEqual([m["id"] for m in inconsistent()], ["s-010"])

    def test_consistent_sample_not_in_inconsistent_list(self):
        parse(SECTIONED_REPORT)
        out = summary("s-002")
        self.assertTrue(out["consistent"])
        self.assertEqual(inconsistent(), [])

    def test_types_returns_canonical_four(self):
        self.assertEqual(
            types()["types"], ["network", "file", "process", "registry"])


class BatchTest(unittest.TestCase):
    def setUp(self):
        reset()
        parse(FLAT_REPORT)
        parse(SECTIONED_REPORT)
        parse(TEXT_REPORT)

    def test_batch_merges_same_action_across_samples(self):
        out = batch()
        self.assertEqual(out["sample_count"], 3)
        shared = next(b for b in out["behaviors"]
                      if b["action"] == "connect 10.0.0.5:443")
        # s-001 出现 2 次 + s-003 出现 2 次
        self.assertEqual(shared["count"], 4)
        self.assertEqual(shared["samples"], ["s-001", "s-003"])

    def test_batch_respects_explicit_ids_and_type_filter(self):
        out = batch(["s-001", "s-003"], "network")
        self.assertEqual(out["sample_ids"], ["s-001", "s-003"])
        self.assertTrue(all(b["type"] == "network" for b in out["behaviors"]))

    def test_batch_collects_anomalies_and_mismatches(self):
        out = batch()
        sample_ids = {a["sample_id"] for a in out["anomalies"]}
        self.assertEqual(sample_ids, {"s-001", "s-002", "s-003"})
        # s-001 静态声明不写文件，实际有写入；s-003 没有任何布尔断言，
        # 按“没声明不臆测”视为一致；s-002 三项声明全部与行为吻合
        self.assertEqual(out["inconsistent_samples"], ["s-001"])
        mismatch = out["mismatches"][0]
        self.assertEqual(mismatch["sample_id"], "s-001")
        self.assertEqual(mismatch["feature"], "file_write")

    def test_batch_unknown_id_raises(self):
        with self.assertRaises(ValueError):
            batch(["s-404"])

    def test_reparse_same_id_replaces_result(self):
        again = parse(FLAT_REPORT)
        self.assertEqual(again["id"], "s-001")
        self.assertEqual(batch()["sample_count"], 3)


if __name__ == "__main__":
    unittest.main()
