import unittest

from pcapflow import DEFAULT_VERSION, analyze, load, report, reset


def pkt(no, src, dst, dport, proto="tcp", sport=12345, **extra):
    row = {"no": no, "src": src, "dst": dst, "sport": sport,
           "dport": dport, "proto": proto, "ts": float(no)}
    row.update(extra)
    return row


# 10.0.0.5 对备份服务器 10.0.0.9 的 SMB 备份任务（白名单，不应再误报），
# 外加一次正常外联。
BACKUP_PACKETS = [
    pkt(1, "10.0.0.5", "10.0.0.9", 445),
    pkt(2, "10.0.0.5", "10.0.0.9", 445),
    pkt(3, "10.0.0.5", "10.0.0.9", 445),
    pkt(4, "10.0.0.5", "203.0.113.7", 443),
]


class SessionizationTest(unittest.TestCase):
    def setUp(self):
        reset()
        load("cap1", BACKUP_PACKETS)

    def test_five_tuple_merges_packets_into_sessions(self):
        out = analyze("cap1")
        # 4 个包归并成 2 条会话，同会话不再逐包重复计数
        self.assertEqual(out["sessions"], 2)
        smb = next(s for s in out["session_list"] if s["dport"] == 445)
        self.assertEqual(smb["packet_count"], 3)
        self.assertEqual(smb["packets"], [1, 2, 3])

    def test_reverse_packets_belong_to_same_session(self):
        packets = BACKUP_PACKETS + [
            pkt(5, "10.0.0.9", "10.0.0.5", 12345, sport=445),
        ]
        load("cap_rev", packets)
        out = analyze("cap_rev")
        smb = next(s for s in out["session_list"] if s["dport"] == 445)
        self.assertEqual(out["sessions"], 2)
        self.assertEqual(smb["packets"], [1, 2, 3, 5])

    def test_direction_classified(self):
        out = analyze("cap1")
        directions = {s["dport"]: s["direction"] for s in out["session_list"]}
        self.assertEqual(directions[445], "lateral")
        self.assertEqual(directions[443], "outbound")


class BackupTrafficTest(unittest.TestCase):
    def setUp(self):
        reset()
        load("cap1", BACKUP_PACKETS)

    def test_backup_smb_not_flagged_abnormal_port(self):
        out = analyze("cap1")
        smb_hits = [h for h in out["hits"] if h["dport"] == 445]
        self.assertEqual(smb_hits, [])

    def test_external_connection_detected(self):
        out = analyze("cap1")
        ext = [h for h in out["hits"] if h["type"] == "external_conn"]
        self.assertEqual(len(ext), 1)
        self.assertEqual(ext[0]["dst"], "203.0.113.7")
        # 证据包序号可直接回放
        self.assertEqual(ext[0]["evidence"], [4])


class RuleVersionTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_rule_version_in_output_and_report(self):
        load("cap1", BACKUP_PACKETS)
        out = analyze("cap1")
        self.assertEqual(out["rule_version"], DEFAULT_VERSION)
        rep = report("cap1")
        self.assertEqual(rep["rule_version"], DEFAULT_VERSION)
        self.assertTrue(all(v["rule_version"] for v in rep["rule_versions"]))

    def test_reject_unknown_version(self):
        load("cap1", BACKUP_PACKETS)
        with self.assertRaises(ValueError):
            analyze("cap1", rule_version="9.9.9")


if __name__ == "__main__":
    unittest.main()
