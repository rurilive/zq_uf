import unittest

from tlsc import (
    ConfigError,
    UnknownEndpointError,
    UnknownHostError,
    check,
    compare,
    expiring,
    recheck,
    register,
    remove,
    report,
    reset,
    update,
)
from tlsc import rules


class ProbeTestCase(unittest.TestCase):
    def setUp(self):
        reset()


class CoverageTest(ProbeTestCase):
    def test_all_endpoints_returned(self):
        out = check("shop.example.com")
        self.assertEqual(out["endpoint_count"], 3)
        self.assertEqual([e["endpoint"] for e in out["endpoints"]],
                         ["主入口", "移动端", "旧支付回调"])

    def test_each_endpoint_has_own_conclusion(self):
        out = check("shop.example.com")
        verdicts = {e["endpoint"]: e["verdict"] for e in out["endpoints"]}
        self.assertEqual(verdicts["主入口"], "fail")      # RC4 + TLSv1.0
        self.assertEqual(verdicts["移动端"], "warn")      # 仅中危
        self.assertEqual(verdicts["旧支付回调"], "fail")  # SSLv3
        self.assertEqual(out["verdict"], "fail")

    def test_weak_endpoint_not_missed_when_main_is_clean(self):
        # cdn 两个入口一致，没有弱项；api 的主入口干净，但另外两个入口有弱项。
        self.assertEqual(check("cdn.example.com")["weak_count"], 0)
        api = check("api.example.com")
        weak_endpoints = {item["endpoint"] for item in api["weak"]}
        self.assertEqual(weak_endpoints, {"内网调用", "IPv6 入口"})
        self.assertNotIn("主入口", api["weak_by_endpoint"])
        self.assertEqual(api["verdict"], "fail")

    def test_check_all_hosts_covers_every_endpoint(self):
        for host in ("shop.example.com", "api.example.com", "cdn.example.com"):
            out = check(host)
            for endpoint in out["endpoints"]:
                name = endpoint["endpoint"]
                grouped = out["weak_by_endpoint"].get(name, [])
                self.assertEqual(grouped, endpoint["weak"], f"{host}/{name}")

    def test_check_single_endpoint(self):
        out = check("shop.example.com", endpoint="移动端")
        self.assertEqual(out["endpoint_count"], 1)
        self.assertEqual(out["endpoints"][0]["endpoint"], "移动端")
        self.assertNotIn("主入口", out["weak_by_endpoint"])

    def test_unknown_host_and_endpoint_raise(self):
        with self.assertRaises(UnknownHostError):
            check("mail.example.com")
        with self.assertRaises(UnknownEndpointError):
            check("shop.example.com", endpoint="不存在的入口")
        # 错误类型仍是 ValueError，旧调用方不受影响
        self.assertIsInstance(UnknownHostError("x"), ValueError)


class WeakItemTest(ProbeTestCase):
    def test_items_categorized_with_actual_and_expected(self):
        out = check("shop.example.com", endpoint="主入口")
        categories = {item["category"] for item in out["weak"]}
        self.assertEqual(categories, {"protocol", "cipher", "certificate"})
        for item in out["weak"]:
            self.assertIn("actual", item)
            self.assertIn("expected", item)
            self.assertTrue(item["actual"])
            self.assertTrue(item["expected"])
        rc4 = next(item for item in out["weak"] if item["actual"] == "RC4-SHA")
        self.assertEqual(rc4["category"], "cipher")
        self.assertEqual(rc4["severity"], "critical")

    def test_weak_items_sorted_by_severity_within_endpoint(self):
        out = check("shop.example.com")
        for endpoint in out["endpoints"]:
            ranks = [rules.severity_rank(item["severity"]) for item in endpoint["weak"]]
            self.assertEqual(ranks, sorted(ranks, reverse=True), endpoint["endpoint"])
        # 域名视图整体按入口排列，首个入口最严重
        self.assertEqual(out["weak"][0]["severity"], "critical")

    def test_items_grouped_by_endpoint_in_host_view(self):
        out = check("shop.example.com")
        order = [e["endpoint"] for e in out["endpoints"]]
        endpoints = [item["endpoint"] for item in out["weak"]]
        # 同一域名的弱项按入口排列，而不是全部打散
        self.assertEqual(endpoints, sorted(
            endpoints, key=lambda name: order.index(name)))

    def test_protocol_grading(self):
        items = {item["actual"]: item["severity"] for item
                 in rules.protocol_items("h", "e", ["SSLv3", "TLSv1.0", "TLSv1.1", "TLSv1.2"])}
        self.assertEqual(items, {"SSLv3": "critical", "TLSv1.0": "high", "TLSv1.1": "medium"})

    def test_cipher_grading(self):
        cases = {
            "RC4-SHA": "critical",
            "DES-CBC3-SHA": "high",
            "AES128-SHA": "medium",
            "AES256-GCM": "low",
            "ECDHE-RSA-AES128-GCM-SHA256": None,
            "TLS_AES_256_GCM_SHA384": None,
        }
        for suite, expected in cases.items():
            self.assertEqual(rules.cipher_severity(suite)[0], expected, suite)

    def test_certificate_issues_included(self):
        out = check("shop.example.com", endpoint="旧支付回调")
        cert_items = [item for item in out["weak"] if item["category"] == "certificate"]
        actuals = {item["actual"] for item in cert_items}
        self.assertIn("签发链不完整", actuals)
        self.assertTrue(any("剩余 3 天" == item["actual"] for item in cert_items))
        certificate = out["endpoints"][0]["certificate"]
        self.assertFalse(certificate["chain_complete"])
        self.assertEqual(len(certificate["chain"]), 1)

    def test_redirect_reported_and_affects_verdict(self):
        out = check("shop.example.com")
        by_endpoint = {e["endpoint"]: e for e in out["endpoints"]}
        self.assertTrue(by_endpoint["主入口"]["redirect_ok"])
        self.assertFalse(by_endpoint["移动端"]["redirect_ok"])
        self.assertIn("未配置 HTTP 到 HTTPS 的重定向", by_endpoint["移动端"]["notes"])
        self.assertFalse(out["redirect_ok"])


class ExpiryTest(ProbeTestCase):
    def test_expiring_lists_each_endpoint_with_days(self):
        out = expiring(30)
        self.assertEqual(out["hosts"], ["shop.example.com"])
        rows = {(row["host"], row["endpoint"]): row["days_left"] for row in out["endpoints"]}
        self.assertEqual(rows[("shop.example.com", "旧支付回调")], 3)
        self.assertEqual(rows[("shop.example.com", "主入口")], 12)
        self.assertEqual(rows[("shop.example.com", "移动端")], 12)

    def test_expiring_sorted_by_days(self):
        days = [row["days_left"] for row in expiring(30)["endpoints"]]
        self.assertEqual(days, sorted(days))

    def test_within_window_is_configurable(self):
        self.assertEqual(expiring(60)["hosts"], ["cdn.example.com", "shop.example.com"])
        self.assertEqual(expiring(300)["hosts"],
                         ["api.example.com", "cdn.example.com", "shop.example.com"])
        self.assertEqual(expiring(1)["hosts"], [])

    def test_expired_certificate_is_critical(self):
        update("cdn.example.com", "主入口", days_left=0)
        out = check("cdn.example.com", endpoint="主入口")
        self.assertEqual(out["endpoints"][0]["certificate"]["status"], "expired")
        severity = {item["actual"]: item["severity"] for item in out["weak"]}
        self.assertEqual(severity["剩余 0 天（已过期）"], "critical")


class ReportTest(ProbeTestCase):
    def test_report_covers_all_endpoints(self):
        out = report()
        self.assertEqual(out["hosts"], ["api.example.com", "cdn.example.com", "shop.example.com"])
        self.assertEqual(out["endpoints"], 8)
        self.assertGreater(out["weak_count"], 0)

    def test_report_groups_by_category_and_severity(self):
        out = report()
        self.assertEqual(set(out["weak_by_category"]), {"protocol", "cipher", "certificate"})
        flattened = [item for items in out["weak_by_category"].values() for item in items]
        self.assertEqual(len(flattened), out["weak_count"])
        self.assertEqual(out["weak_by_severity"]["critical"][0]["actual"], "SSLv3")

    def test_report_weak_items_follow_endpoint_order_within_host(self):
        out = report()
        for host, grouped in out["weak_by_host"].items():
            order = [e["endpoint"] for e in check(host)["endpoints"]]
            flat = [item["endpoint"] for items in grouped.values() for item in items]
            self.assertEqual(flat, sorted(flat, key=lambda name: order.index(name)))

    def test_report_verdicts(self):
        out = report()
        self.assertEqual(out["verdict"]["ok"], ["cdn.example.com"])
        self.assertEqual(out["verdict"]["fail"], ["api.example.com", "shop.example.com"])


class CompareTest(ProbeTestCase):
    def test_compare_lists_field_groups(self):
        out = compare("shop.example.com")
        self.assertIn("protocols", out["divergent"])
        groups = out["fields"]["protocols"]["groups"]
        self.assertEqual(len(groups), 3)
        self.assertFalse(out["consistent"])

    def test_compare_points_to_weaker_deviations(self):
        out = compare("api.example.com")
        # 内网调用多开了一个 CBC 套件，IPv6 入口多开了 TLSv1.0 且证书更早到期
        self.assertEqual(out["deviations"]["内网调用"], ["ciphers"])
        self.assertIn("protocols", out["deviations"]["IPv6 入口"])
        self.assertEqual(out["weakest_endpoint"], "IPv6 入口")

    def test_compare_consistent_host(self):
        out = compare("cdn.example.com")
        self.assertTrue(out["consistent"])
        self.assertEqual(out["divergent"], [])
        self.assertIsNone(out["weakest_endpoint"])

    def test_compare_ignores_order_of_protocol_list(self):
        update("cdn.example.com", "备用节点", protocols=["TLSv1.3", "TLSv1.2"])
        self.assertTrue(compare("cdn.example.com")["consistent"])


class RecheckTest(ProbeTestCase):
    def test_first_recheck_checks_everything(self):
        out = recheck()
        self.assertEqual(out["changed"], 8)
        self.assertEqual(out["unchanged"], 0)

    def test_recheck_after_check_only_rechecks_changed_endpoint(self):
        check("shop.example.com")
        out = recheck("shop.example.com")
        self.assertEqual(out["changed"], 0)
        self.assertEqual(len(out["skipped"]), 3)

        update("shop.example.com", "旧支付回调", days_left=365)
        out = recheck("shop.example.com")
        self.assertEqual(out["checked"], ["shop.example.com/旧支付回调"])
        self.assertEqual(out["unchanged"], 2)

    def test_recheck_reflects_remediation(self):
        check("shop.example.com")
        update("shop.example.com", "旧支付回调",
               protocols=["TLSv1.2", "TLSv1.3"],
               ciphers=["ECDHE-RSA-AES128-GCM-SHA256"],
               redirect="https://shop.example.com/",
               chain_complete=True,
               days_left=365)
        out = recheck("shop.example.com")
        row = out["hosts"][0]
        fixed = next(e for e in row["endpoints"] if e["endpoint"] == "旧支付回调")
        self.assertEqual(fixed["verdict"], "ok")
        self.assertEqual(fixed["weak"], [])

    def test_recheck_with_different_window_rechecks(self):
        check("shop.example.com", within=30)
        out = recheck("shop.example.com", within=7)
        self.assertEqual(out["changed"], 3)
        self.assertEqual(out["unchanged"], 0)

    def test_recheck_unknown_host_raises(self):
        with self.assertRaises(UnknownHostError):
            recheck("mail.example.com")


class InventoryTest(ProbeTestCase):
    def test_register_new_endpoint(self):
        register("cdn.example.com", "灰度节点", {
            "address": "10.1.0.33",
            "port": 443,
            "days_left": 400,
            "protocols": ["TLSv1.2", "TLSv1.3"],
            "ciphers": ["ECDHE-RSA-AES128-GCM-SHA256"],
            "redirect": "https://cdn.example.com/",
            "chain_complete": True,
            "chain": ["cdn.example.com", "Example Intermediate CA", "Example Root CA"],
        })
        out = check("cdn.example.com")
        self.assertEqual(out["endpoint_count"], 3)
        self.assertEqual(out["weak_count"], 0)

    def test_update_records_change(self):
        result = update("api.example.com", "IPv6 入口", protocols=["TLSv1.2", "TLSv1.3"])
        self.assertEqual(result["changed"], ["protocols"])
        self.assertEqual(result["before"]["protocols"], ["TLSv1.0", "TLSv1.2"])
        self.assertEqual(report()["changes"][-1]["action"], "update")

    def test_remove_endpoint(self):
        removed = remove("api.example.com", "IPv6 入口")
        self.assertEqual(removed["address"], "2001:db8::21")
        self.assertEqual(check("api.example.com")["endpoint_count"], 2)

    def test_removed_endpoint_not_revived_by_recheck(self):
        check("shop.example.com")
        remove("shop.example.com", "移动端")
        out = recheck("shop.example.com")
        row = out["hosts"][0]
        self.assertNotIn("移动端", [e["endpoint"] for e in row["endpoints"]])
        self.assertEqual(out["unchanged"], 2)

    def test_update_rejects_unknown_field(self):
        with self.assertRaises(ConfigError):
            update("shop.example.com", "主入口", tls_version="TLSv1.2")

    def test_invalid_config_raises(self):
        bad = {
            "address": "10.1.0.40",
            "port": 443,
            "days_left": 10,
            "protocols": ["TLSv2.0"],
            "ciphers": ["ECDHE-RSA-AES128-GCM-SHA256"],
        }
        with self.assertRaises(ConfigError):
            register("cdn.example.com", "坏协议", bad)
        with self.assertRaises(ConfigError):
            register("cdn.example.com", "坏天数", dict(bad, protocols=["TLSv1.2"], days_left=True))
        with self.assertRaises(ConfigError):
            register("cdn.example.com", "坏端口", dict(bad, protocols=["TLSv1.2"], port=70000))

    def test_registered_weak_endpoint_is_caught(self):
        register("cdn.example.com", "第三方节点", {
            "address": "10.1.0.34",
            "port": 443,
            "days_left": 200,
            "protocols": ["TLSv1.0"],
            "ciphers": ["RC4-SHA"],
            "redirect": None,
            "chain_complete": True,
            "chain": [],
        })
        out = check("cdn.example.com")
        self.assertEqual(out["verdict"], "fail")
        self.assertIn("第三方节点", out["weak_by_endpoint"])


if __name__ == "__main__":
    unittest.main()
