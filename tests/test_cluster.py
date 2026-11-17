import os
import tempfile
import unittest

from honeypot import (
    adjust,
    clear_adjustments,
    cluster,
    load_adjustments,
    report,
    reset,
    save_adjustments,
)


def batch_scan(ip, paths, ua, start=100, gap=2):
    """同一工具按固定间隔扫一组路径，模拟批量扫描。"""
    return [
        {"ip": ip, "path": path, "at": start + i * gap, "ua": ua}
        for i, path in enumerate(paths)
    ]


class ClusterTest(unittest.TestCase):
    def setUp(self):
        reset()

    # ------------------------------------------------ 基本聚类

    def test_same_behavior_different_ips_merge(self):
        # 换地址的同一批 nikto 扫描必须合进一个组，而不是各算新来源。
        events = []
        for ip in ("203.0.113.10", "203.0.113.11", "203.0.113.12"):
            events += batch_scan(ip, ["/admin", "/login", "/.git/config"], "nikto/2.1.6")
        out = cluster(events)
        self.assertEqual(out["groups_count"], 1)
        group = out["groups"][0]
        self.assertEqual(group["sources"], 3)
        self.assertEqual(group["events"], 9)
        self.assertEqual(group["first"], 100)
        self.assertEqual(group["last"], 104)
        self.assertTrue(group["features"]["tools"][0]["core"])
        self.assertEqual(group["features"]["tools"][0]["name"], "nikto")
        self.assertFalse(group["singleton"])

    def test_different_behavior_stays_separate(self):
        events = (
            batch_scan("203.0.113.10", ["/admin", "/login"], "nikto")
            + batch_scan("198.51.100.20", ["/.env", "/wp-config.php"], "gobuster/3.1")
        )
        out = cluster(events)
        self.assertEqual(out["groups_count"], 2)
        self.assertEqual(sorted(g["sources"] for g in out["groups"]), [1, 1])
        self.assertEqual(set(out["singletons"]), {g["id"] for g in out["groups"]})

    def test_group_keeps_extracted_features(self):
        events = batch_scan(
            "203.0.113.10", ["/admin", "/admin"], "nikto",
        )
        out = cluster(events)
        group = out["groups"][0]
        paths = {item["name"]: item for item in group["features"]["paths"]}
        self.assertEqual(paths["/admin"]["events"], 2)
        self.assertEqual(group["features"]["interval_median"], 2)
        self.assertEqual(group["first"], 100)
        self.assertEqual(group["last"], 102)
        # 签名稳定存在，供下一轮引用同一类扫描。
        self.assertEqual(len(group["signature"]), 12)

    # ------------------------------------------------ 离群事件

    def test_event_off_group_path_is_outlier(self):
        # 两个地址重复打同一核心路径，构成典型批量扫描组。
        events = batch_scan("203.0.113.10", ["/admin"] * 4, "nikto")
        events += batch_scan("203.0.113.11", ["/admin"] * 4, "nikto", start=200)
        # 第二个地址夹带一条偏离组特征的针对性探测。
        events.append({"ip": "203.0.113.11", "path": "/internal/api/users",
                       "at": 206, "ua": "nikto"})
        out = cluster(events)
        self.assertEqual(len(out["outliers"]), 1)
        outlier = out["outliers"][0]
        self.assertEqual(outlier["path"], "/internal/api/users")
        self.assertEqual(outlier["reasons"], ["path"])
        # 离群事件仍计入组统计。
        self.assertEqual(out["groups"][0]["events"], 9)

    def test_interval_outlier_flagged(self):
        events = batch_scan("203.0.113.10", ["/admin", "/login", "/.git"], "nikto", gap=2)
        events.append({"ip": "203.0.113.10", "path": "/admin",
                       "at": 100 + 200, "ua": "nikto"})
        out = cluster(events)
        reasons = {o["at"]: o["reasons"] for o in out["outliers"]}
        self.assertIn("interval", reasons[300])

    def test_singletons_listed_for_followup(self):
        events = (
            batch_scan("203.0.113.10", ["/admin", "/login"], "nikto")
            + batch_scan("198.51.100.20", ["/.env"], "sqlmap/1.7")
        )
        out = cluster(events)
        singleton = next(g for g in out["groups"] if g["sources"] == 1
                         and g["features"]["tools"][0]["name"] == "sqlmap")
        self.assertIn(singleton["id"], out["singletons"])
        self.assertIn(singleton["id"], report()["singletons"])

    # ------------------------------------------------ 跨组关系

    def test_ip_crossing_groups_keeps_relations(self):
        # 混合地址：主行为是 nikto，顺带摸了一个 sqlmap 组的路径。
        mixed = batch_scan("203.0.113.30", ["/admin", "/login", "/.git"], "nikto")
        mixed.append({"ip": "203.0.113.30", "path": "/db/query", "at": 500,
                      "ua": "sqlmap/1.7"})
        events = (
            batch_scan("203.0.113.10", ["/admin", "/login", "/.git"], "nikto")
            + batch_scan("198.51.100.20", ["/db/query", "/db/dump"], "sqlmap/1.7")
            + mixed
        )
        out = cluster(events)
        groups = {g["id"]: g for g in out["groups"]}
        mixed_member = next(
            member for group in out["groups"]
            for member in group["members"] if member["ip"] == "203.0.113.30"
        )
        self.assertTrue(mixed_member["also_in"], "跨组关系应保留在成员明细里")
        relation = next(
            rel for group in out["groups"] for rel in group["relations"]
            if rel["ip"] == "203.0.113.30"
        )
        self.assertIn("/db/query", relation["shared_paths"])
        self.assertIn("sqlmap", relation["shared_tools"])
        # 每个地址只主归属一组。
        owners = [g["id"] for g in out["groups"]
                  for m in g["members"] if m["ip"] == "203.0.113.30"]
        self.assertEqual(len(owners), 1)

    # ------------------------------------------------ 人工调整

    def test_merge_adjustment_applies_next_round(self):
        events = (
            batch_scan("203.0.113.10", ["/admin", "/login"], "nikto")
            + batch_scan("198.51.100.20", ["/.env", "/wp-config.php"], "gobuster/3.1")
        )
        first = cluster(events)
        self.assertEqual(first["groups_count"], 2)
        ids = sorted(g["id"] for g in first["groups"])
        adjust("merge", sources=[ids[1]], into=ids[0], label="web-scanners")

        second = cluster(events)
        self.assertEqual(second["groups_count"], 1)
        group = second["groups"][0]
        self.assertEqual(group["label"], "web-scanners")
        self.assertEqual(group["sources"], 2)

    def test_pin_moves_ip_primary_group(self):
        events = (
            batch_scan("203.0.113.10", ["/admin", "/login"], "nikto")
            + batch_scan("198.51.100.20", ["/.env"], "gobuster/3.1")
        )
        first = cluster(events)
        # 组 id 随规模重排、签名随成员变化，跨轮引用应使用人工标注名。
        target_sig = next(g["signature"] for g in first["groups"]
                          if "gobuster" in g["label"])
        adjust("label", group=target_sig, label="env-hunters")
        adjust("pin", ip="203.0.113.10", group="env-hunters")
        second = cluster(events)
        group = next(g for g in second["groups"] if g["label"] == "env-hunters")
        self.assertIn("203.0.113.10", [m["ip"] for m in group["members"]])
        owners = [g["id"] for g in second["groups"]
                  for m in g["members"] if m["ip"] == "203.0.113.10"]
        self.assertEqual(len(owners), 1)

    def test_adjustments_roundtrip_file(self):
        events = batch_scan("203.0.113.10", ["/admin", "/login"], "nikto")
        first = cluster(events)
        sig = first["groups"][0]["signature"]
        adjust("label", group=sig, label="daily-nikto-crew")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "adjustments.json")
            save_adjustments(path)
            reset()
            load_adjustments(path)
            out = cluster(events)
            self.assertEqual(out["groups"][0]["label"], "daily-nikto-crew")

    def test_unmerge_restores_separate_groups(self):
        events = (
            batch_scan("203.0.113.10", ["/admin", "/login"], "nikto")
            + batch_scan("198.51.100.20", ["/.env"], "gobuster/3.1")
        )
        first = cluster(events)
        ids = sorted(g["id"] for g in first["groups"])
        adjust("merge", sources=[ids[1]], into=ids[0])
        self.assertEqual(cluster(events)["groups_count"], 1)
        adjust("unmerge", sources=[ids[1]], into=ids[0])
        self.assertEqual(cluster(events)["groups_count"], 2)

    # ------------------------------------------------ 按天汇总

    def test_daily_summary(self):
        events = [
            {"ip": "203.0.113.10", "path": "/admin", "at": 10, "day": "2026-11-16",
             "ua": "nikto"},
            {"ip": "203.0.113.10", "path": "/admin", "at": 20, "day": "2026-11-16",
             "ua": "nikto"},
            {"ip": "203.0.113.11", "path": "/admin", "at": 90000, "day": "2026-11-17",
             "ua": "nikto"},
        ]
        out = cluster(events)
        self.assertEqual(out["groups_count"], 1)
        group = out["groups"][0]
        self.assertEqual(group["daily"], [
            {"day": "2026-11-16", "events": 2, "sources": 1},
            {"day": "2026-11-17", "events": 1, "sources": 1},
        ])
        self.assertEqual(report()["daily"], [
            {"day": "2026-11-16", "groups": 1, "sources": 1, "events": 2},
            {"day": "2026-11-17", "groups": 1, "sources": 1, "events": 1},
        ])

    # ------------------------------------------------ 入参与报告

    def test_report_orders_by_events(self):
        events = (
            batch_scan("203.0.113.10", ["/a", "/b", "/c"], "nikto")
            + batch_scan("198.51.100.20", ["/x"], "gobuster/3.1")
        )
        cluster(events)
        top = report()["top"]
        self.assertEqual(top[0]["events"], 3)
        self.assertEqual(report()["events"], 4)
        self.assertEqual(report()["sources"], 2)

    def test_missing_field_raises(self):
        with self.assertRaises(ValueError):
            cluster([{"ip": "203.0.113.10", "at": 10}])
        with self.assertRaises(ValueError):
            cluster("not-a-list")

    def test_reset_clears_state_and_adjustments(self):
        cluster(batch_scan("203.0.113.10", ["/admin"], "nikto"))
        adjust("label", group="G0001", label="x")
        reset()
        self.assertEqual(report()["groups"], 0)
        self.assertEqual(report()["adjustments"]["labels"], 0)
        clear_adjustments()  # 重复调用不报错


if __name__ == "__main__":
    unittest.main()
