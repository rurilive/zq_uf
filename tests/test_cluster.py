import unittest

from honeypot import cluster, report, reset


def ev(ip, path, at, ua=None):
    event = {"ip": ip, "path": path, "at": at}
    if ua:
        event["ua"] = ua
    return event


def scan_events(ips, paths, start=0, step=10, ua="nikto"):
    """模拟一批来源用相同工具按相同节奏扫相同路径。"""
    events = []
    for ip in ips:
        for i, path in enumerate(paths * 2):
            events.append(ev(ip, path, start + i * step, ua))
    return events


class ClusterTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_same_behavior_merges_sources(self):
        out = cluster(scan_events(["203.0.113.7", "198.51.100.4"], ["/admin"]))
        self.assertEqual(len(out["groups"]), 1)
        group = out["groups"][0]
        self.assertEqual(group["source_count"], 2)
        self.assertEqual(group["count"], 4)
        self.assertEqual(group["first"], 0)
        self.assertEqual(group["last"], 10)
        self.assertEqual(out["sources"], 2)

    def test_features_kept_in_group(self):
        out = cluster(scan_events(["203.0.113.7"], ["/admin", "/.env"]))
        features = out["groups"][0]["features"]
        self.assertEqual(features["paths"], ["/.env", "/admin"])
        self.assertEqual(features["tool"], "nikto")
        self.assertEqual(features["interval"], 10)

    def test_different_tool_separate_groups(self):
        out = cluster(
            scan_events(["203.0.113.7"], ["/admin"], ua="nikto")
            + scan_events(["198.51.100.4"], ["/admin"], ua="gobuster")
        )
        self.assertEqual(len(out["groups"]), 2)
        self.assertEqual({g["features"]["tool"] for g in out["groups"]},
                         {"nikto", "gobuster"})

    def test_path_ids_normalized(self):
        out = cluster([
            ev("203.0.113.7", "/user/123", 10, "curl"),
            ev("203.0.113.7", "/user/456", 20, "curl"),
            ev("198.51.100.4", "/user/789", 30, "curl"),
            ev("198.51.100.4", "/user/321", 40, "curl"),
        ])
        self.assertEqual(len(out["groups"]), 1)
        self.assertEqual(out["groups"][0]["features"]["paths"], ["/user/{n}"])

    def test_outlier_marked(self):
        events = scan_events(["203.0.113.7", "198.51.100.4"], ["/admin", "/.env"])
        events.append(ev("203.0.113.7", "/backup.sql", 55, "nikto"))
        out = cluster(events)
        self.assertEqual(len(out["groups"]), 1)
        self.assertEqual(len(out["outliers"]), 1)
        outlier = out["outliers"][0]
        self.assertEqual(outlier["path"], "/backup.sql")
        self.assertEqual(outlier["group"], out["groups"][0]["id"])

    def test_shared_source_primary_group(self):
        events = scan_events(["203.0.113.7", "198.51.100.4"], ["/admin"], ua="nikto")
        events.append(ev("203.0.113.7", "/private", 500, "curl"))
        out = cluster(events)
        self.assertEqual(len(out["groups"]), 2)
        nikto_group = [g for g in out["groups"] if g["features"]["tool"] == "nikto"][0]
        curl_group = [g for g in out["groups"] if g["features"]["tool"] == "curl"][0]
        # 主要行为是 nikto 批量扫描，curl 组里只保留 shared 关系
        self.assertIn("203.0.113.7", nikto_group["sources"])
        self.assertEqual(curl_group["sources"], [])
        self.assertEqual(curl_group["shared"], ["203.0.113.7"])

    def test_report_daily_summary(self):
        day1 = 1700000000  # 2023-11-14 UTC
        day2 = 1700086400  # 2023-11-15 UTC
        out = cluster([
            ev("203.0.113.7", "/admin", day1, "nikto"),
            ev("203.0.113.7", "/admin", day1 + 10, "nikto"),
            ev("198.51.100.4", "/admin", day2, "nikto"),
        ])
        daily = {row["day"]: row for row in out["daily"]}
        self.assertEqual(daily["2023-11-14"]["events"], 2)
        self.assertEqual(daily["2023-11-15"]["events"], 1)
        self.assertEqual(daily["2023-11-15"]["sources"], 1)
        self.assertEqual(report()["daily"], out["daily"])

    def test_prior_keeps_group_ids(self):
        events = scan_events(["203.0.113.7"], ["/admin"])
        first = cluster(events)
        second = cluster(events, prior=first)
        self.assertEqual([g["id"] for g in first["groups"]],
                         [g["id"] for g in second["groups"]])

    def test_adjust_merge(self):
        out = cluster(
            scan_events(["203.0.113.7"], ["/admin"], ua="nikto")
            + scan_events(["198.51.100.4"], ["/admin"], ua="gobuster")
        )
        ids = [g["id"] for g in out["groups"]]
        merged = cluster(
            scan_events(["203.0.113.7"], ["/admin"], ua="nikto")
            + scan_events(["198.51.100.4"], ["/admin"], ua="gobuster"),
            adjust={"merge": [ids]},
        )
        self.assertEqual(len(merged["groups"]), 1)
        self.assertEqual(merged["groups"][0]["id"], ids[0])
        self.assertEqual(merged["groups"][0]["count"], out["events"])
        # 并入组的事件偏离目标组工具特征，标为离群
        kept = merged["groups"][0]["features"]["tool"]
        dropped = {"nikto", "gobuster"} - {kept}
        self.assertEqual({o["tool"] for o in merged["outliers"]}, dropped)

    def test_adjust_move(self):
        events = scan_events(["203.0.113.7"], ["/admin"], ua="nikto")
        events += scan_events(["198.51.100.4"], ["/.env"], ua="nikto")
        out = cluster(events)
        self.assertEqual(len(out["groups"]), 2)
        target = [g for g in out["groups"] if "/.env" in g["features"]["paths"]][0]
        moved = cluster(events, adjust={"move": {"203.0.113.7": target["id"]}})
        self.assertEqual(len(moved["groups"]), 1)
        self.assertEqual(moved["groups"][0]["source_count"], 2)

    def test_adjust_unknown_group_raises(self):
        with self.assertRaises(ValueError):
            cluster(scan_events(["203.0.113.7"], ["/admin"]),
                    adjust={"move": {"203.0.113.7": "g99"}})

    def test_missing_path_raises(self):
        with self.assertRaises(ValueError):
            cluster([{"ip": "203.0.113.7", "at": 10}])

    def test_report_empty_before_cluster(self):
        out = report()
        self.assertEqual(out["groups"], [])
        self.assertEqual(out["events"], 0)


if __name__ == "__main__":
    unittest.main()
