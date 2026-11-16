import json
import os
import tempfile
import unittest

from pcapflow import (
    FEEDBACK,
    analyze,
    get_packet,
    load,
    mark_false_positive,
    query,
    report,
    reset,
    run_cases,
)
from pcapflow.feedback import FeedbackStore
from tests.test_flow import pkt


def build_pcap():
    return [
        pkt(1, "10.0.0.5", "10.0.0.9", 445),
        pkt(2, "10.0.0.5", "10.0.0.9", 445),
        pkt(3, "10.0.0.5", "203.0.113.7", 443),
        pkt(4, "10.0.0.5", "203.0.113.8", 443),
    ]


class ReportTest(unittest.TestCase):
    def setUp(self):
        reset()
        load("cap1", build_pcap())
        analyze("cap1")

    def test_report_groups_by_host(self):
        rep = report("cap1")
        self.assertIn("10.0.0.5", rep["hosts"])
        host = rep["hosts"]["10.0.0.5"]
        self.assertEqual(host["types"].get("external_conn"), 2)
        self.assertEqual(rep["types"]["external_conn"], 2)
        self.assertEqual(rep["rule_version"], "1.1.0")

    def test_report_aggregates_multiple_pcaps(self):
        load("cap2", [pkt(10, "10.0.0.8", "198.51.100.9", 80)])
        analyze("cap2")
        rep = report()
        self.assertEqual(set(rep["pcaps"]), {"cap1", "cap2"})
        self.assertIn("10.0.0.8", rep["hosts"])

    def test_query_drilldown_by_host_and_time(self):
        rows = query(host="203.0.113.7")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "external_conn")

        rows = query(host="10.0.0.5", start=3.5, end=3.5)
        self.assertEqual(rows, [])

        rows = query(host="10.0.0.5", start=2.5, end=3.5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dst"], "203.0.113.7")

    def test_query_filter_by_type(self):
        rows = query(type="external_conn")
        self.assertEqual({h["dst"] for h in rows},
                         {"203.0.113.7", "203.0.113.8"})

    def test_evidence_packet_replay(self):
        rows = query(type="external_conn")
        packet = get_packet("cap1", rows[0]["evidence"][0])
        self.assertEqual(packet["dst"], rows[0]["dst"])
        self.assertEqual(packet["no"], rows[0]["evidence"][0])


class FeedbackTest(unittest.TestCase):
    def setUp(self):
        reset()
        load("cap1", build_pcap())
        analyze("cap1")

    def _external_sid(self, dst="203.0.113.7"):
        return query(host=dst)[0]["session_id"]

    def test_mark_false_positive_suppresses_hit(self):
        sid = self._external_sid()
        case = mark_false_positive("cap1", sid, "external_conn", reason="已知更新服务")
        out = analyze("cap1")
        self.assertFalse(any(h["session_id"] == sid for h in out["hits"]))
        sess = next(s for s in out["session_list"] if s["session_id"] == sid)
        self.assertIn("external_conn", sess["suppressed"])
        self.assertEqual(case["reason"], "已知更新服务")

    def test_mark_unknown_rule_raises(self):
        sid = self._external_sid()
        with self.assertRaises(ValueError):
            mark_false_positive("cap1", sid, "tunnel")

    def test_case_export_import_roundtrip(self):
        sid = self._external_sid()
        mark_false_positive("cap1", sid, "external_conn", reason="白名单外联")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cases.json")
            FEEDBACK.export_cases(path)
            with open(path, encoding="utf-8") as fh:
                rows = json.load(fh)
            self.assertEqual(rows[0]["session_id"], sid)

            reset()
            load("cap1", build_pcap())
            n = FEEDBACK.import_cases(path)
            self.assertEqual(n, 1)
            out = analyze("cap1")
            self.assertFalse(any(h["session_id"] == sid for h in out["hits"]))

    def test_run_cases_detects_regression(self):
        sid = self._external_sid()
        mark_false_positive("cap1", sid, "external_conn", reason="白名单")
        results = run_cases(FEEDBACK, lambda p: analyze(p), ["cap1"])
        self.assertEqual(results[0]["status"], "pass")

        # 规则收紧后原本不告警的会话重新命中：用独立用例库跑回归，
        # run_cases 应识别出规则再次告警
        from pcapflow import build_ruleset, register_ruleset

        # 先造一组在宽松规则下不构成扫描的内网会话
        reset()
        scan_rows = [
            pkt(i, "10.0.0.7", f"10.0.2.{i}", 22, sport=50000 + i)
            for i in range(1, 4)
        ]
        load("scan_cap", scan_rows)
        loose = build_ruleset(
            "1.1.0", name="loose",
            overrides={"lateral_scan": {"scan_host_threshold": 10}},
        )
        strict = build_ruleset(
            "1.1.0", name="tight",
            overrides={"lateral_scan": {"scan_host_threshold": 2}},
        )
        register_ruleset(loose)
        register_ruleset(strict)

        loose_out = analyze("scan_cap", ruleset_name="loose", use_feedback=False)
        self.assertEqual(
            [h for h in loose_out["hits"] if h["type"] == "lateral_scan"], []
        )

        scan_sid = loose_out["session_list"][0]["session_id"]
        store = FeedbackStore()
        store._cases[scan_sid] = {
            "case_id": "manual0001", "pcap": "scan_cap", "session_id": scan_sid,
            "five_tuple": [], "rule_id": "lateral_scan",
            "type": "lateral_scan", "reason": "已知运维巡检", "packets": [1],
        }
        # 宽松版本下规则不命中 -> 用例通过
        ok_results = run_cases(
            store, lambda p: analyze(p, ruleset_name="loose", use_feedback=False),
            ["scan_cap"],
        )
        self.assertEqual(ok_results[0]["status"], "pass")

        # 收紧版本下规则重新告警且未抑制 -> 回归
        reg_results = run_cases(
            store, lambda p: analyze(p, ruleset_name="tight", use_feedback=False),
            ["scan_cap"],
        )
        self.assertEqual(reg_results[0]["status"], "regressed")


if __name__ == "__main__":
    unittest.main()
