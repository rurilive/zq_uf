import unittest

from pcapflow import analyze, load, report

PACKETS = [
    {"no": 1, "src": "10.0.0.5", "dst": "10.0.0.9", "dport": 445, "proto": "tcp"},
    {"no": 2, "src": "10.0.0.5", "dst": "10.0.0.9", "dport": 445, "proto": "tcp"},
    {"no": 3, "src": "10.0.0.5", "dst": "10.0.0.9", "dport": 445, "proto": "tcp"},
    {"no": 4, "src": "10.0.0.5", "dst": "203.0.113.7", "dport": 443, "proto": "tcp"},
]


class FlowTest(unittest.TestCase):
    def setUp(self):
        load("cap1", PACKETS)

    def test_same_session_counted_per_packet(self):
        out = analyze("cap1")
        self.assertEqual(out["sessions"], 3)
        self.assertEqual([hit["packet"] for hit in out["hits"]], [1, 2, 3])

    def test_backup_traffic_flagged_as_abnormal_port(self):
        out = analyze("cap1")
        self.assertEqual(out["hits"][0]["type"], "abnormal_port")
        self.assertEqual(out["hits"][0]["dport"], 445)

    def test_rule_version_left_empty(self):
        out = analyze("cap1")
        self.assertEqual(out["rule_version"], "")
        self.assertEqual(out["failed"], [])

    def test_report_groups_by_type_and_host(self):
        analyze("cap1")
        rep = report()
        self.assertEqual(rep["hits"], 3)
        self.assertEqual(rep["types"], {"abnormal_port": 3})
        self.assertEqual(rep["hosts"], {"10.0.0.5": 3})

    def test_reject_empty_packets(self):
        with self.assertRaises(ValueError):
            load("cap2", [])


if __name__ == "__main__":
    unittest.main()
