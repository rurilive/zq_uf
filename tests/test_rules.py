import unittest

from pcapflow import analyze, load, reset
from tests.test_flow import pkt


def scan_packets(hosts, source="10.0.0.50"):
    rows = []
    no = 1
    for host in hosts:
        rows.append(pkt(no, source, host, 22, sport=40000 + no))
        no += 1
    return rows


class LateralScanTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_scan_detected_when_fanout_exceeds_threshold(self):
        # 默认 1.1.0 阈值为 3 台内网主机
        hosts = [f"10.0.1.{i}" for i in range(10, 14)]
        load("scan", scan_packets(hosts))
        out = analyze("scan")
        scans = [h for h in out["hits"] if h["type"] == "lateral_scan"]
        self.assertTrue(scans)
        sids = {h["session_id"] for h in scans}
        self.assertEqual(len(sids), 4)
        # 每条命中都带证据包序号
        self.assertTrue(all(h["evidence"] for h in scans))

    def test_few_internal_peers_is_not_scan(self):
        hosts = [f"10.0.1.{i}" for i in range(10, 12)]
        load("small", scan_packets(hosts))
        out = analyze("small", rule_version="1.1.0")
        self.assertNotIn("lateral_scan", out["hits"] and {h["type"] for h in out["hits"]})

    def test_backup_server_whitelist_excluded_from_fanout(self):
        # 全打备份服务器 10.0.0.9（白名单），不构成扫描
        load("backup", scan_packets(["10.0.0.9", "10.0.0.9", "10.0.0.9"]))
        out = analyze("backup")
        self.assertFalse(any(h["type"] == "lateral_scan" for h in out["hits"]))


class TunnelTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_tls_on_non_standard_port_is_tunnel(self):
        load("t1", [pkt(1, "10.0.0.5", "203.0.113.9", 8443, app="tls")])
        out = analyze("t1")
        self.assertIn("tunnel", {h["type"] for h in out["hits"]})

    def test_long_dns_query_is_tunnel(self):
        long_name = "a" * 70 + ".example.com"
        load("dns", [
            pkt(1, "10.0.0.5", "10.0.0.53", 53, proto="udp", sport=53000,
                dns_query=long_name),
        ])
        out = analyze("dns")
        tunnel = [h for h in out["hits"] if h["type"] == "tunnel"]
        self.assertEqual(len(tunnel), 1)
        self.assertIn("DNS", tunnel[0]["reason"])

    def test_normal_dns_not_tunnel(self):
        load("dns_ok", [
            pkt(1, "10.0.0.5", "10.0.0.53", 53, proto="udp", sport=53001,
                dns_query="example.com"),
        ])
        out = analyze("dns_ok")
        self.assertFalse(any(h["type"] == "tunnel" for h in out["hits"]))


class AbnormalPortTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_unknown_port_flagged(self):
        load("p1", [pkt(1, "10.0.0.5", "203.0.113.1", 31337)])
        out = analyze("p1")
        self.assertIn("abnormal_port", {h["type"] for h in out["hits"]})

    def test_known_app_on_non_baseline_port_explains_itself(self):
        load("p2", [pkt(1, "10.0.0.5", "203.0.113.1", 9000, app="http")])
        out = analyze("p2")
        hit = next(h for h in out["hits"] if h["type"] == "abnormal_port")
        self.assertIn("http", hit["reason"])

    def test_backup_port_not_abnormal(self):
        load("p3", [pkt(1, "10.0.0.5", "10.0.0.9", 445)])
        out = analyze("p3")
        self.assertFalse(any(h["type"] == "abnormal_port" for h in out["hits"]))


if __name__ == "__main__":
    unittest.main()
