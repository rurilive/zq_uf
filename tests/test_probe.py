import unittest
from dataclasses import replace

from tlsc import Checker, check, diff_entries, expiring, mutate_entry, recheck, report
from tlsc.models import (
    CATEGORY_CERT,
    CATEGORY_CIPHER,
    CATEGORY_PROTOCOL,
    CATEGORY_REDIRECT,
)
from tlsc.probe import _HOSTS


def fresh_registry():
    """复制一份登记数据（Entry 不可变，可安全共享）。"""
    return {host: list(entries) for host, entries in _HOSTS.items()}


def entry_of(result, entry_id):
    for row in result["entries"]:
        if row["entry_id"] == entry_id:
            return row
    raise KeyError(entry_id)


class MultiEntryCheckTest(unittest.TestCase):
    def test_every_endpoint_gets_its_own_conclusion(self):
        out = check("shop.example.com")
        self.assertEqual(out["endpoint_count"], 3)
        self.assertEqual(
            [row["entry_id"] for row in out["entries"]],
            ["primary", "backup", "http-redirect"],
        )

    def test_redirect_and_chain_are_part_of_conclusion(self):
        row = entry_of(check("api.example.com"), "primary")
        self.assertEqual(row["redirect"]["status"], "302")
        self.assertTrue(row["redirect"]["target"].startswith("https://"))
        self.assertEqual(len(row["issuer_chain"]), 3)

    def test_unknown_host_still_raises(self):
        with self.assertRaises(ValueError):
            check("mail.example.com")


class WeaknessClassificationTest(unittest.TestCase):
    def test_protocol_and_cipher_weakness_carry_actual_and_expected(self):
        primary = entry_of(check("shop.example.com"), "primary")
        categories = {item["category"] for item in primary["weak_items"]}
        self.assertIn(CATEGORY_PROTOCOL, categories)
        self.assertIn(CATEGORY_CIPHER, categories)

        protocol_item = next(i for i in primary["weak_items"] if i["category"] == CATEGORY_PROTOCOL)
        self.assertEqual(protocol_item["actual"], "TLSv1.0")
        self.assertIn("TLSv1.3", protocol_item["expected"])
        self.assertEqual(protocol_item["severity"], "high")

        cipher_item = next(i for i in primary["weak_items"] if i["category"] == CATEGORY_CIPHER)
        self.assertEqual(cipher_item["actual"], "RC4-SHA")
        self.assertEqual(cipher_item["severity"], "critical")

    def test_legacy_cbc_sha1_cipher_is_medium(self):
        backup = entry_of(check("shop.example.com"), "backup")
        item = next(i for i in backup["weak_items"] if i["category"] == CATEGORY_CIPHER)
        self.assertEqual(item["actual"], "AES128-SHA")
        self.assertEqual(item["severity"], "medium")

    def test_cert_issues_split_into_self_signed_incomplete_and_expired(self):
        legacy = entry_of(check("cdn.example.com"), "edge-legacy")
        cert_items = [i for i in legacy["weak_items"] if i["category"] == CATEGORY_CERT]
        kinds = {i["item"] for i in cert_items}
        self.assertIn("cert_expired", kinds)
        self.assertIn("cert_self_signed", kinds)
        expired = next(i for i in cert_items if i["item"] == "cert_expired")
        self.assertEqual(expired["severity"], "critical")

        primary = entry_of(check("shop.example.com"), "primary")
        chain_item = next(
            i for i in primary["weak_items"] if i["item"] == "cert_chain_incomplete"
        )
        self.assertEqual(chain_item["severity"], "medium")

    def test_http_entry_without_https_redirect_is_weak(self):
        plain = entry_of(check("api.example.com"), "http-redirect")
        self.assertTrue(plain["weak"])
        item = plain["weak_items"][0]
        self.assertEqual(item["category"], CATEGORY_REDIRECT)
        self.assertEqual(item["severity"], "medium")
        self.assertEqual(item["actual"], "不跳转")

    def test_http_entry_with_https_redirect_is_clean(self):
        plain = entry_of(check("shop.example.com"), "http-redirect")
        self.assertFalse(plain["weak"])
        self.assertEqual(plain["weak_items"], [])

    def test_healthy_entry_has_no_weakness(self):
        primary = entry_of(check("api.example.com"), "primary")
        self.assertFalse(primary["weak"])
        self.assertEqual(primary["weak_count"], 0)


class ExpiryTest(unittest.TestCase):
    def test_expire_soon_flag_uses_days_left(self):
        shop = check("shop.example.com")
        self.assertTrue(entry_of(shop, "primary")["expire_soon"])
        api = check("api.example.com")
        self.assertFalse(entry_of(api, "primary")["expire_soon"])

    def test_expired_cert_is_not_flagged_expire_soon_but_listed_as_expired(self):
        cdn = check("cdn.example.com")
        legacy = entry_of(cdn, "edge-legacy")
        self.assertFalse(legacy["expire_soon"])
        self.assertEqual(cdn["expired"], ["edge-legacy"])

    def test_expiring_lists_days_ahead_sorted_by_urgency(self):
        out = expiring(30)
        self.assertEqual(out["within"], 30)
        self.assertEqual(
            [(row["host"], row["entry_id"], row["days_left"]) for row in out["entries"]],
            [
                ("cdn.example.com", "edge-legacy", -3),
                ("shop.example.com", "primary", 12),
            ],
        )

    def test_expiring_threshold_widens_window(self):
        out = expiring(60)
        self.assertEqual(
            out["hosts"], ["cdn.example.com", "shop.example.com"]
        )
        self.assertEqual(len(out["entries"]), 4)  # edge-a/edge-b 45 天也进入窗口


class ReportTest(unittest.TestCase):
    def test_report_covers_all_endpoints_and_groups_by_host(self):
        out = report()
        self.assertEqual(
            out["hosts"],
            ["api.example.com", "cdn.example.com", "shop.example.com"],
        )
        self.assertEqual(out["endpoints"], 9)
        self.assertEqual(
            {row["host"]: row["endpoint_count"] for row in out["by_host"]},
            {
                "api.example.com": 3,
                "cdn.example.com": 3,
                "shop.example.com": 3,
            },
        )

    def test_weak_items_ranked_by_severity_with_location(self):
        out = report()
        severities = [row["severity"] for row in out["weak_items"]]
        ranks = [{"critical": 0, "high": 1, "medium": 2, "low": 3}[s] for s in severities]
        self.assertEqual(ranks, sorted(ranks))
        # 最紧急的一项：cdn 边缘老节点证书已过期
        first = out["weak_items"][0]
        self.assertEqual(first["host"], "cdn.example.com")
        self.assertEqual(first["entry_id"], "edge-legacy")
        self.assertEqual(first["item"], "cert_expired")
        self.assertEqual(out["severity_summary"],
                         {"critical": 3, "high": 4, "medium": 3, "low": 1})

    def test_report_weak_entry_count_and_expiring_entries(self):
        out = report()
        self.assertEqual(out["weak_entries"], 5)
        self.assertEqual(
            {(row["host"], row["entry_id"]) for row in out["expiring_entries"]},
            {
                ("cdn.example.com", "edge-legacy"),
                ("shop.example.com", "primary"),
            },
        )


class DiffEntriesTest(unittest.TestCase):
    def test_diff_only_returns_fields_that_differ(self):
        out = diff_entries("cdn.example.com")
        self.assertIn("protocol", out["differences"])
        self.assertEqual(out["differences"]["protocol"]["edge-legacy"], "SSLv3")
        self.assertEqual(out["differences"]["protocol"]["edge-a"], "TLSv1.2")
        # 三个入口端口/协议簇一致的字段不出现
        self.assertNotIn("port", out["differences"])
        self.assertNotIn("scheme", out["differences"])

    def test_diff_redirect_across_entries(self):
        out = diff_entries("shop.example.com")
        self.assertEqual(out["differences"]["redirect"]["http-redirect"], "301")
        self.assertIsNone(out["differences"]["redirect"]["primary"])


class IncrementalRecheckTest(unittest.TestCase):
    def setUp(self):
        self.registry = fresh_registry()
        self.checker = Checker(self.registry)

    def test_first_check_baselines_every_entry(self):
        out = self.checker.check("shop.example.com")
        self.assertEqual(sorted(out["rechecked"]), ["backup", "http-redirect", "primary"])

    def test_unchanged_entries_are_cached_not_rechecked(self):
        self.checker.check("shop.example.com")
        out = self.checker.recheck("shop.example.com")
        self.assertEqual(out["rechecked"], [])
        self.assertEqual(sorted(out["cached"]), ["backup", "http-redirect", "primary"])
        self.assertTrue(all(row["changed"] is False for row in out["entries"]))

    def test_only_changed_entry_is_rechecked(self):
        self.checker.check("shop.example.com")
        entries = self.registry["shop.example.com"]
        idx = next(i for i, e in enumerate(entries) if e.entry_id == "primary")
        entries[idx] = replace(
            entries[idx],
            protocol="TLSv1.3",
            cipher="TLS_AES_256_GCM_SHA384",
        )
        out = self.checker.recheck("shop.example.com")
        self.assertEqual(out["rechecked"], ["primary"])
        self.assertEqual(out["changed"], ["primary"])
        self.assertEqual(sorted(out["cached"]), ["backup", "http-redirect"])

        primary = entry_of(out, "primary")
        self.assertTrue(primary["changed"])
        self.assertEqual(primary["protocol"], "TLSv1.3")
        # 整改后协议/套件弱项消失，但到期与链问题仍在
        self.assertTrue(
            all(
                item["category"] in (CATEGORY_CERT,)
                for item in primary["weak_items"]
            )
        )
        backup = entry_of(out, "backup")
        self.assertFalse(backup["changed"])

    def test_force_rechecks_everything(self):
        self.checker.check("shop.example.com")
        out = self.checker.recheck("shop.example.com", force=True)
        self.assertEqual(sorted(out["rechecked"]), ["backup", "http-redirect", "primary"])
        self.assertEqual(out["cached"], [])

    def test_new_entry_is_rechecked_as_new(self):
        out = self.checker.recheck("cdn.example.com")
        self.assertEqual(sorted(out["new"]), ["edge-a", "edge-b", "edge-legacy"])
        again = self.checker.recheck("cdn.example.com")
        self.assertEqual(again["new"], [])

    def test_recheck_unknown_host_raises(self):
        with self.assertRaises(ValueError):
            self.checker.recheck("mail.example.com")

    def test_module_level_recheck_after_mutate_then_restore(self):
        original = next(e for e in _HOSTS["api.example.com"] if e.entry_id == "canary")
        try:
            recheck("api.example.com")  # 建基线
            mutate_entry("api.example.com", "canary", protocol="TLSv1.2")
            out = recheck("api.example.com")
            self.assertEqual(out["changed"], ["canary"])
            canary = entry_of(out, "canary")
            self.assertEqual(canary["protocol"], "TLSv1.2")
        finally:
            mutate_entry("api.example.com", "canary", protocol=original.protocol)


if __name__ == "__main__":
    unittest.main()
