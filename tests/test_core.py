import unittest

from assetmerge import MergeAbort, merge, preview, report, scan_list, split

# 同一台主机 web1 的域名、IP、端口三条记录；web2 为另一台主机。
ASSETS = [
    {"id": "a1", "host": "web1", "kind": "domain", "value": "www.example.com",
     "vulns": ["vuln-sqli"], "source": "crm"},
    {"id": "a2", "host": "web1", "kind": "ip", "value": "10.0.0.8",
     "vulns": ["vuln-sqli"], "source": "netscan"},
    {"id": "a3", "host": "web1", "kind": "port", "value": "10.0.0.8:443",
     "vulns": ["vuln-xss"], "source": "netscan"},
    {"id": "a4", "host": "web2", "kind": "ip", "value": "10.0.0.9",
     "vulns": ["vuln-xss"], "source": "netscan"},
]

# 别名域名没有显式 host，只能靠解析地址合并进同一台主机。
RESOLVED_ASSETS = [
    {"id": "b1", "host": "web1", "kind": "ip", "value": "10.0.0.8",
     "vulns": []},
    {"id": "b2", "host": "alias-host", "kind": "domain",
     "value": "alias.example.com", "resolves_to": ["10.0.0.8"],
     "vulns": ["vuln-xss"]},
    {"id": "b3", "host": "other", "kind": "domain",
     "value": "other.example.com", "resolves_to": ["10.0.0.9"],
     "vulns": []},
]

# 两条端口记录属于同一实例（同一 IP），且该 IP 没有独立 ip 记录；
# 两条记录 host 不同，保证只能靠 port-instance 依据合并。
PORT_ASSETS = [
    {"id": "p1", "host": "h1", "kind": "port", "value": "10.0.0.8:443",
     "vulns": ["vuln-a"]},
    {"id": "p2", "host": "h2", "kind": "port", "value": "10.0.0.8:8080",
     "vulns": ["vuln-a", "vuln-b"]},
]


class MergeTest(unittest.TestCase):
    def test_same_host_merged_into_one_group(self):
        out = merge(ASSETS)
        self.assertEqual(out["count"], 4)            # 原始记录数守恒
        self.assertEqual(out["group_count"], 2)      # 合并后只剩两台主机
        self.assertEqual(out["alias_count"], 2)      # web1 的 a2、a3 进别名
        self.assertEqual(out["basis"], "host")

        web1 = next(g for g in out["groups"] if g["host"] == "web1")
        self.assertEqual(web1["primary_record"], "a1")
        self.assertEqual(web1["merged_count"], 3)
        self.assertEqual(web1["basis"], ["host", "port-instance"])
        self.assertEqual({a["id"] for a in web1["aliases"]}, {"a2", "a3"})
        # 别名与来源保留在边上
        self.assertEqual(web1["sources"], ["crm", "netscan"])
        alias_a2 = next(a for a in web1["aliases"] if a["id"] == "a2")
        self.assertEqual(alias_a2["value"], "10.0.0.8")
        self.assertEqual(alias_a2["source"], "netscan")

    def test_vuln_deduped_with_affected_entries(self):
        merge(ASSETS)
        rep = report()
        self.assertEqual(rep["hosts"], 2)
        self.assertEqual(rep["records"], 4)
        # 同一漏洞只留一条，不再按记录累加
        self.assertEqual(rep["vuln_total"], 2)
        self.assertEqual(set(rep["vulns"]), {"vuln-sqli", "vuln-xss"})
        sqli = rep["vulns"]["vuln-sqli"]
        self.assertEqual(sqli["host_count"], 1)
        # 入口按记录 id 顺序收集并去重
        self.assertEqual(sqli["entries"],
                         ["www.example.com", "10.0.0.8"])
        xss = rep["vulns"]["vuln-xss"]
        self.assertEqual(xss["host_count"], 2)  # web1 与 web2 各有一处

    def test_scan_list_dispatches_per_host(self):
        merge(ASSETS)
        # 合并后按主机下发：web1 一条、web2 一条，主记录 id 升序
        self.assertEqual(scan_list(), ["a1", "a4"])

    def test_report_host_distribution(self):
        merge(ASSETS)
        dist = {row["host"]: row for row in report()["host_distribution"]}
        self.assertEqual(dist["web1"]["asset_count"], 3)
        self.assertEqual(dist["web1"]["vuln_count"], 2)
        self.assertEqual(dist["web2"]["asset_count"], 1)
        self.assertEqual(dist["web2"]["vuln_count"], 1)


class BasisTest(unittest.TestCase):
    def test_resolved_ip_merges_alias_domain(self):
        out = merge(RESOLVED_ASSETS)
        # b2 解析到 10.0.0.8，与 b1 合并；b3 独立
        self.assertEqual(out["group_count"], 2)
        web1 = next(g for g in out["groups"] if g["primary_record"] == "b1")
        self.assertEqual(web1["basis"], ["resolved-ip"])
        self.assertEqual({a["id"] for a in web1["aliases"]}, {"b2"})
        # 别名上的入口不再漏掉：漏洞入口包含别名域名
        rep = report()
        self.assertEqual(rep["vulns"]["vuln-xss"]["entries"],
                         ["alias.example.com"])

    def test_port_instance_basis(self):
        out = merge(PORT_ASSETS)
        self.assertEqual(out["group_count"], 1)
        group = out["groups"][0]
        self.assertEqual(group["basis"], ["port-instance"])
        self.assertEqual(group["merged_count"], 2)

    def test_no_merge_uses_record_basis(self):
        out = merge([{"id": "s1", "host": "sole", "kind": "ip",
                      "value": "10.0.0.1", "vulns": []}])
        self.assertEqual(out["basis"], "record")
        self.assertEqual(out["groups"][0]["basis"], [])
        self.assertEqual(report()["basis"], "record")


class PreviewTest(unittest.TestCase):
    def test_preview_does_not_change_state(self):
        merge(ASSETS)
        before = report()
        preview(RESOLVED_ASSETS)  # 预演另一张表，不应生效
        self.assertEqual(report(), before)

    def test_preview_reports_affected_count(self):
        pv = preview(ASSETS)
        self.assertTrue(pv["dry_run"])
        self.assertEqual(pv["affected_count"], 3)   # 只有 web1 的 3 条受影响
        self.assertEqual(pv["merged_group_count"], 1)
        self.assertEqual(pv["count"], 4)

    def test_merge_accepts_matching_preview(self):
        pv = preview(ASSETS)
        out = merge(ASSETS, expect=pv)
        self.assertEqual(out["group_count"], 2)

    def test_merge_aborts_on_preview_mismatch_and_keeps_state(self):
        merge(ASSETS)
        stale = preview(PORT_ASSETS)  # 与实际 ASSETS 不一致的预览
        with self.assertRaises(MergeAbort) as ctx:
            merge(ASSETS, expect=stale)
        diffs = ctx.exception.differences
        self.assertTrue(diffs)
        fields = {d["field"] for d in diffs}
        self.assertIn("count", fields)
        # 中止后状态保持合并前那次 merge 的结果，没有被部分写入
        self.assertEqual(report()["records"], 4)
        self.assertEqual(scan_list(), ["a1", "a4"])


class SplitTest(unittest.TestCase):
    def test_split_by_group_restores_originals(self):
        out = merge(ASSETS)
        group_id = next(g["group_id"] for g in out["groups"]
                        if g["host"] == "web1")
        result = split(group_id=group_id)

        # 只回退指定合并：web1 拆成 3 条独立记录，web2 保持原样
        self.assertEqual(result["group_count"], 4)
        self.assertEqual(result["count"], 4)  # 资产总数不多不少
        split_record = result["splits"][0]
        self.assertEqual(split_record["restored_records"],
                         ["a1", "a2", "a3"])
        self.assertEqual(split_record["basis"], ["host", "port-instance"])

        rep = report()
        self.assertEqual(rep["hosts"], 4)
        self.assertEqual(rep["records"], 4)
        # 拆分后恢复按记录下发
        self.assertEqual(scan_list(), ["a1", "a2", "a3", "a4"])

    def test_split_by_record_id_only_affects_its_group(self):
        merge(ASSETS)
        result = split(record_id="a2")  # 指定 web1 组内的一条
        hosts = {g["host"]: g for g in result["groups"]}
        # web1 的三条全部回到独立分组
        self.assertEqual([g["primary_record"]
                          for g in result["groups"]],
                         ["a1", "a2", "a3", "a4"])
        # web2 分组没有被动过
        self.assertEqual(hosts["web2"]["merged_count"], 1)

    def test_split_singleton_rejected(self):
        merge(ASSETS)
        with self.assertRaises(ValueError):
            split(record_id="a4")  # web2 本来就是单条分组

    def test_split_keeps_other_merged_group_intact(self):
        # 两张主机都发生合并，拆其中一个，另一个的结构不变
        assets = ASSETS + [
            {"id": "a5", "host": "web2", "kind": "port",
             "value": "10.0.0.9:22", "vulns": []},
        ]
        out = merge(assets)
        web2 = next(g for g in out["groups"] if g["host"] == "web2")
        split(group_id=web2["group_id"])
        rep = report()
        dist = {row["host"]: row for row in rep["host_distribution"]}
        self.assertEqual(dist["web1"]["asset_count"], 3)  # 仍是合并态
        self.assertEqual(dist["web2"]["asset_count"], 1)
        self.assertEqual(rep["records"], 5)               # 总数守恒


class ConservationTest(unittest.TestCase):
    def test_count_never_changes_across_operations(self):
        out = merge(ASSETS)
        self.assertEqual(out["count"], 4)
        split(group_id=next(g["group_id"] for g in out["groups"]
                            if g["host"] == "web1"))
        self.assertEqual(report()["records"], 4)
        # 再次 merge 同样回到 4 条，不会凭空多出
        out2 = merge(ASSETS)
        self.assertEqual(out2["count"], 4)

    def test_empty_assets(self):
        out = merge([])
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["groups"], [])
        self.assertEqual(scan_list(), [])
        self.assertEqual(report()["vuln_total"], 0)

    def test_reject_non_list(self):
        with self.assertRaises(ValueError):
            merge("not-a-list")

    def test_reject_duplicate_id(self):
        with self.assertRaises(ValueError):
            merge([
                {"id": "x", "host": "h", "kind": "ip", "value": "1.1.1.1"},
                {"id": "x", "host": "h", "kind": "ip", "value": "1.1.1.2"},
            ])


if __name__ == "__main__":
    unittest.main()
