import unittest

from subdomain import diff, enumerate, load, report, reset, restore

ROWS = [
    {"name": "www.example.com", "ip": "10.0.0.5", "status": "ok"},
    {"name": "web.example.com", "ip": "10.0.0.5", "status": "ok"},
    {"name": "api.example.com", "ip": "10.0.0.9", "status": "ok"},
    {"name": "old.example.com", "ip": "", "status": "fail"},
    {"name": "slow.example.com", "ip": "", "status": "timeout"},
]


class ConvergenceTest(unittest.TestCase):
    def setUp(self):
        reset("example.com")
        load("example.com", ROWS)

    # ---- 同地址合并 ----
    def test_same_ip_names_merged_into_one_entry(self):
        out = enumerate("example.com")
        self.assertEqual(out["total"], 2)
        self.assertEqual(len(out["entries"]), 2)
        self.assertEqual(out["aliases"], 1)
        merged = next(e for e in out["entries"] if e["ip"] == "10.0.0.5")
        self.assertEqual(merged["names"], ["web.example.com", "www.example.com"])

    def test_entry_keeps_every_name(self):
        entries = {e["ip"]: e for e in enumerate("example.com")["entries"]}
        self.assertIn("www.example.com", entries["10.0.0.5"]["names"])
        self.assertIn("web.example.com", entries["10.0.0.5"]["names"])
        self.assertEqual(entries["10.0.0.9"]["names"], ["api.example.com"])

    def test_alias_count_is_extra_names_per_entry(self):
        reset("alias.test")
        load("alias.test", [
            {"name": "a.alias.test", "ip": "10.4.0.1"},
            {"name": "b.alias.test", "ip": "10.4.0.1"},
            {"name": "c.alias.test", "ip": "10.4.0.1"},
            {"name": "solo.alias.test", "ip": "10.4.0.2"},
        ])
        out = enumerate("alias.test")
        self.assertEqual(out["total"], 2)
        # 三个名字一条入口记 2 个别名，单名字入口记 0。
        self.assertEqual(out["aliases"], 2)

    # ---- 通配与无效分开 ----
    def test_wildcard_and_invalid_are_separated(self):
        out = enumerate("example.com")
        self.assertEqual(out["wildcard"], 0)
        self.assertEqual(out["invalid"], 2)
        self.assertEqual(out["failed"], 1)
        self.assertEqual(out["timeout"], 1)

    def test_fail_and_timeout_counted_separately(self):
        rep = report("example.com")
        self.assertEqual(rep["failed_names"], ["old.example.com"])
        self.assertEqual(rep["timeout_names"], ["slow.example.com"])
        self.assertEqual(rep["invalid"], 2)

    def test_report_counts_entries_not_candidates(self):
        rep = report("example.com")
        self.assertEqual(rep["candidates"], 5)
        self.assertEqual(rep["valid"], 2)
        self.assertEqual(rep["aliases"], 1)
        self.assertLess(rep["coverage"], 1.0)

    def test_multi_ip_row_is_one_entry(self):
        reset("multi.test")
        load("multi.test", [
            {"name": "a.multi.test", "ips": ["10.1.1.1", "10.1.1.2"]},
            {"name": "b.multi.test", "ips": ["10.1.1.1", "10.1.1.2"]},
        ])
        out = enumerate("multi.test")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["entries"][0]["ips"], ["10.1.1.1", "10.1.1.2"])
        self.assertEqual(out["aliases"], 1)

    def test_same_name_duplicate_rows_merge(self):
        reset("dup.test")
        load("dup.test", [
            {"name": "x.dup.test", "ip": "10.2.2.2"},
            {"name": "x.dup.test", "ip": "10.2.2.3"},
        ])
        out = enumerate("dup.test")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["entries"][0]["ips"], ["10.2.2.2", "10.2.2.3"])

    def test_shared_address_bridges_entries(self):
        # b 同时指向 a 和 c 的地址，三个名字应并成同一条入口。
        reset("bridge.test")
        load("bridge.test", [
            {"name": "a.bridge.test", "ips": ["10.3.0.1"]},
            {"name": "b.bridge.test", "ips": ["10.3.0.1", "10.3.0.2"]},
            {"name": "c.bridge.test", "ips": ["10.3.0.2"]},
        ])
        out = enumerate("bridge.test")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["entries"][0]["names"],
                         ["a.bridge.test", "b.bridge.test", "c.bridge.test"])
        self.assertEqual(out["entries"][0]["ips"], ["10.3.0.1", "10.3.0.2"])

    def test_returned_entries_do_not_alias_state(self):
        out = enumerate("example.com")
        out["entries"][0]["names"].append("evil.example.com")
        out["entries"][0]["ips"].append("1.2.3.4")
        again = enumerate("example.com")
        self.assertNotIn("evil.example.com", again["entries"][0]["names"])
        self.assertNotIn("1.2.3.4", again["entries"][0]["ips"])


class WildcardTest(unittest.TestCase):
    def setUp(self):
        reset("wild.test")

    def test_explicit_wildcard_status_is_kept_apart(self):
        load("wild.test", [
            {"name": "www.wild.test", "ip": "10.3.3.3", "status": "ok"},
            {"name": "rand1.wild.test", "ip": "10.9.9.9", "status": "wildcard"},
        ])
        out = enumerate("wild.test")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["wildcard"], 1)
        self.assertEqual(out["wildcard_names"], ["rand1.wild.test"])

    def test_probe_row_marks_wildcard_ip(self):
        # 探测行 *.takeover.test 解析到的地址即泛解析地址。
        load("takeover.test", [
            {"name": "*.takeover.test", "ip": "10.4.4.4", "probe": True},
            {"name": "a.takeover.test", "ip": "10.4.4.4"},
            {"name": "b.takeover.test", "ip": "10.4.4.4"},
            {"name": "real.takeover.test", "ip": "10.4.4.8"},
        ])
        out = enumerate("takeover.test")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["entries"][0]["name"], "real.takeover.test")
        self.assertEqual(out["wildcard"], 2)

    def test_wildcard_ips_argument_marks_matches(self):
        load("kw.test", [
            {"name": "a.kw.test", "ip": "10.5.5.5"},
            {"name": "b.kw.test", "ip": "10.5.5.5"},
        ], wildcard_ips=["10.5.5.5"])
        out = enumerate("kw.test")
        self.assertEqual(out["total"], 0)
        self.assertEqual(out["wildcard"], 2)

    def test_wildcard_hits_keep_raw_records(self):
        row = {"name": "rand.wild.test", "ip": "10.9.9.9", "status": "wildcard",
               "ttl": 300}
        load("wild.test", [row])
        out = enumerate("wild.test")
        self.assertEqual(out["wildcard_records"][0]["raw"], row)
        self.assertEqual(out["wildcard_records"][0]["ips"], ["10.9.9.9"])

    def test_scale_inference_marks_wildcard(self):
        rows = [{"name": "real.wild.test", "ip": "10.6.6.1"}]
        rows += [{"name": "n%d.wild.test" % i, "ip": "10.6.6.6"} for i in range(20)]
        load("wild.test", rows)
        out = enumerate("wild.test")
        self.assertEqual(out["wildcard"], 20)
        self.assertEqual(out["total"], 1)

    def test_inference_does_not_touch_small_cluster(self):
        # 几个名字共享一个反向代理地址是正常情况，不能判成泛解析。
        load("wild.test", [
            {"name": "a.wild.test", "ip": "10.6.6.6"},
            {"name": "b.wild.test", "ip": "10.6.6.6"},
            {"name": "c.wild.test", "ip": "10.6.6.1"},
        ])
        out = enumerate("wild.test")
        self.assertEqual(out["wildcard"], 0)
        self.assertEqual(out["total"], 2)

    def test_inference_can_be_disabled(self):
        rows = [{"name": "real.wild.test", "ip": "10.6.6.1"}]
        rows += [{"name": "n%d.wild.test" % i, "ip": "10.6.6.6"} for i in range(20)]
        load("wild.test", rows, wildcard_threshold=0)
        self.assertEqual(enumerate("wild.test")["wildcard"], 0)

    def test_error_is_wildcard_when_restored(self):
        load("wild.test", [
            {"name": "gone.wild.test", "ip": "10.9.9.9", "status": "wildcard"},
        ])
        self.assertEqual(enumerate("wild.test")["wildcard"], 1)
        result = restore("wild.test", "gone.wild.test")
        self.assertTrue(result["restored"])
        out = enumerate("wild.test")
        self.assertEqual(out["wildcard"], 0)
        self.assertEqual(out["total"], 1)

    def test_restore_missreported_wildcard_with_given_ip(self):
        load("wild.test", [
            {"name": "false.wild.test", "ip": "10.9.9.9", "status": "wildcard"},
        ])
        result = restore("wild.test", "false.wild.test", ip="10.7.7.7")
        self.assertTrue(result["restored"])
        entry = enumerate("wild.test")["entries"][0]
        self.assertEqual(entry["name"], "false.wild.test")
        self.assertEqual(entry["ips"], ["10.7.7.7"])


class InvalidAndRoundTest(unittest.TestCase):
    def setUp(self):
        reset("round.test")

    def test_confirmed_invalid_skipped_in_next_round(self):
        rows = [
            {"name": "a.round.test", "ip": "10.8.8.1"},
            {"name": "dead.round.test", "status": "fail"},
        ]
        load("round.test", rows)   # 第一轮：首次解析失败，报一次
        out = enumerate("round.test")
        self.assertEqual(out["failed"], 1)
        self.assertEqual(out["skipped"], 0)
        load("round.test", rows)   # 第二轮：已确认无效，直接跳过
        out = enumerate("round.test")
        self.assertEqual(out["round"], 2)
        self.assertEqual(out["failed"], 0)
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(out["names"], 2)

    def test_first_round_timeout_is_reported(self):
        load("round.test", [{"name": "t.round.test", "status": "timeout"}])
        out = enumerate("round.test")
        self.assertEqual(out["timeout"], 1)
        self.assertEqual(out["invalid"], 1)
        self.assertEqual(out["skipped"], 0)

    def test_timeout_is_not_confirmed_invalid(self):
        # 超时不算“确认无效”，下一轮仍要重试并上报。
        load("round.test", [{"name": "t.round.test", "status": "timeout"}])
        load("round.test", [{"name": "t.round.test", "status": "timeout"}])
        out = enumerate("round.test")
        self.assertEqual(out["timeout"], 1)
        self.assertEqual(out["skipped"], 0)

    def test_restore_brings_back_confirmed_invalid(self):
        load("round.test", [{"name": "dead.round.test", "status": "fail"}])
        load("round.test", [{"name": "dead.round.test", "status": "fail"}])
        self.assertEqual(enumerate("round.test")["skipped"], 1)
        result = restore("round.test", "dead.round.test", ip="10.8.8.9")
        self.assertTrue(result["restored"])
        self.assertEqual(enumerate("round.test")["total"], 1)
        # 恢复后下一轮不再跳过。
        load("round.test", [{"name": "dead.round.test", "status": "fail"}])
        self.assertEqual(enumerate("round.test")["skipped"], 0)

    def test_restore_without_ip_stays_pending(self):
        load("round.test", [{"name": "dead.round.test", "status": "fail"}])
        result = restore("round.test", "dead.round.test")
        self.assertFalse(result["restored"])
        self.assertTrue(result["pending"])
        # 本轮仍是无效，但已进入待重新解析状态并留下审计记录。
        self.assertEqual(enumerate("round.test")["invalid"], 1)
        self.assertEqual(report("round.test")["restored"][0]["pending"], True)
        # 下一轮解析到地址后成为有效入口。
        load("round.test", [{"name": "dead.round.test", "ip": "10.8.8.9"}])
        out = enumerate("round.test")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["invalid"], 0)

    def test_invalid_names_are_skipped_only_after_confirmation(self):
        load("round.test", [{"name": "slow.round.test", "status": "timeout"}])
        load("round.test", [{"name": "slow.round.test", "status": "fail"}])
        out = enumerate("round.test")
        self.assertEqual(out["failed"], 1)
        self.assertEqual(out["skipped"], 0)
        load("round.test", [{"name": "slow.round.test", "status": "fail"}])
        out = enumerate("round.test")
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(out["failed"], 0)

    def test_rounds_stored_separately(self):
        load("round.test", [{"name": "a.round.test", "ip": "10.8.8.1"}])
        load("round.test", [{"name": "b.round.test", "ip": "10.8.8.2"}])
        first = enumerate("round.test", round=1)
        second = enumerate("round.test", round=2)
        self.assertEqual([e["name"] for e in first["entries"]], ["a.round.test"])
        self.assertEqual([e["name"] for e in second["entries"]], ["b.round.test"])
        self.assertEqual(len(report("round.test")["rounds"]), 2)

    def test_restore_record_from_earlier_round(self):
        # 通配命中只在第一轮出现，最新一轮里恢复也应当生效。
        load("round.test", [{"name": "w.round.test", "ip": "10.9.9.9",
                             "status": "wildcard"}])
        load("round.test", [{"name": "a.round.test", "ip": "10.8.8.1"}])
        self.assertEqual(enumerate("round.test")["wildcard"], 0)
        result = restore("round.test", "w.round.test")
        self.assertTrue(result["restored"])
        out = enumerate("round.test")
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["wildcard"], 0)
        # 恢复记录记在最新一轮上，运营在最新一轮看得到。
        restored = report("round.test")["restored"]
        self.assertEqual([item["name"] for item in restored], ["w.round.test"])
        # 更早轮次的原始结果保持不变，历史可回溯。
        first = enumerate("round.test", round=1)
        self.assertEqual(first["wildcard_names"], ["w.round.test"])

    def test_only_new_filters_already_reported(self):
        load("round.test", [{"name": "a.round.test", "ip": "10.8.8.1"}])
        load("round.test", [
            {"name": "a.round.test", "ip": "10.8.8.1"},
            {"name": "b.round.test", "ip": "10.8.8.2"},
        ])
        out = enumerate("round.test", only_new=True)
        self.assertEqual(out["new_names"], ["b.round.test"])
        self.assertEqual([e["name"] for e in out["entries"]], ["b.round.test"])

    def test_diff_reports_only_added(self):
        load("round.test", [
            {"name": "a.round.test", "ip": "10.8.8.1"},
            {"name": "gone.round.test", "ip": "10.8.8.3"},
        ])
        load("round.test", [
            {"name": "a.round.test", "ip": "10.8.8.1"},
            {"name": "b.round.test", "ip": "10.8.8.2"},
        ])
        out = diff("round.test")
        self.assertEqual(out["added"], 1)
        self.assertEqual(out["added_names"], ["b.round.test"])
        self.assertEqual(out["removed_names"], ["gone.round.test"])
        self.assertEqual([e["name"] for e in out["added_entries"]], ["b.round.test"])


class ValidationTest(unittest.TestCase):
    def setUp(self):
        reset("valid.test")

    def test_reject_unknown_domain(self):
        with self.assertRaises(ValueError):
            enumerate("nope.example.com")

    def test_reject_unknown_round(self):
        load("valid.test", [{"name": "a.valid.test", "ip": "10.0.0.1"}])
        with self.assertRaises(ValueError):
            enumerate("valid.test", round=9)

    def test_reject_rows_not_list(self):
        with self.assertRaises(ValueError):
            load("valid.test", {"name": "a.valid.test"})

    def test_reject_name_missing(self):
        with self.assertRaises(ValueError):
            load("valid.test", [{"ip": "10.0.0.1"}])

    def test_ok_status_without_ip_is_invalid(self):
        load("valid.test", [{"name": "x.valid.test", "status": "ok"}])
        self.assertEqual(enumerate("valid.test")["invalid"], 1)

    def test_names_normalized(self):
        load("valid.test", [
            {"name": " WWW.Valid.Test. ", "ip": "10.0.0.1"},
            {"name": "www.valid.test", "ip": "10.0.0.1"},
        ])
        out = enumerate("valid.test")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["entries"][0]["names"], ["www.valid.test"])


if __name__ == "__main__":
    unittest.main()
