import unittest

from auditlog import (
    append,
    boundary,
    chain,
    days,
    get_algorithm,
    register_algorithm,
    set_algorithm,
    tail,
    verify,
)

DAY1 = {"day": "2026-01-01", "action": "login", "user": "alice"}
DAY2 = {"day": "2026-01-02", "action": "read", "user": "bob"}


def seed(n=5):
    out = []
    for i in range(n):
        day = "2026-01-%02d" % (i // 2 + 1)
        out.append(append({"day": day, "action": "act-%d" % i, "time": "t%d" % i}))
    return out


class ChainTest(unittest.TestCase):
    def setUp(self):
        chain._records.clear()
        chain._DEFAULT_ALGORITHM = "sha256"

    # --- append ---------------------------------------------------------

    def test_append_starts_at_zero(self):
        self.assertEqual(append(DAY1)["seq"], 0)

    def test_first_record_has_empty_link(self):
        self.assertEqual(append(DAY1)["link"], "")

    def test_append_links_previous_digest(self):
        first = append(DAY1)
        second = append(DAY2)
        self.assertEqual(second["link"], first["digest"])
        self.assertNotEqual(second["digest"], first["digest"])

    def test_append_returns_metadata(self):
        result = append(DAY1)
        self.assertEqual(result["day"], "2026-01-01")
        self.assertEqual(result["algorithm"], "sha256")
        self.assertTrue(result["digest"])

    def test_append_rejects_non_dict(self):
        with self.assertRaises(ValueError):
            append(["not", "a", "dict"])

    # --- 逐条验证与篡改定位 --------------------------------------------

    def test_verify_ok(self):
        seed(4)
        result = verify()
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 4)
        self.assertIsNone(result["broken"])

    def test_detects_content_tamper(self):
        seed(4)
        chain._records[2]["record"]["action"] = "deleted"
        result = verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 2)
        self.assertEqual(result["reason"], "digest")

    def test_link_is_covered_by_digest(self):
        # 改 link 而不重算摘要 —— 摘要先失配
        seed(4)
        chain._records[2]["link"] = "f" * 16
        result = verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 2)
        self.assertEqual(result["reason"], "digest")

    def test_detects_self_consistent_forgery(self):
        # 改 link 并按新 link 重算摘要，摘要自洽但接不上前一条
        seed(4)
        entry = chain._records[2]
        entry["link"] = "f" * 16
        entry["digest"] = chain._digest(
            entry["record"], entry["link"], entry["algorithm"],
            entry["day"], entry["time"], entry["seq"],
        )
        result = verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 2)
        self.assertEqual(result["reason"], "link")
        self.assertEqual(result["break"]["found_link"], "f" * 16)

    def test_break_names_prev_and_next(self):
        seed(4)
        chain._records[2]["record"]["action"] = "deleted"
        result = verify()
        info = result["break"]
        self.assertEqual(info["prev"]["seq"], 1)
        self.assertEqual(info["next"]["seq"], 2)
        self.assertEqual(info["prev"]["time"], "t1")
        self.assertEqual(info["next"]["time"], "t2")
        self.assertEqual(result["broken_time"], "t2")
        self.assertEqual(info["expected_link"], chain._records[1]["digest"])
        self.assertEqual(info["found_link"], chain._records[2]["link"])

    def test_impact_range_reported(self):
        seed(6)
        chain._records[3]["record"]["action"] = "deleted"
        impact = verify()["impact"]
        self.assertEqual(impact["start"], 3)
        self.assertEqual(impact["end"], 5)
        self.assertEqual(impact["count"], 3)

    # --- 区间校验 -------------------------------------------------------

    def test_seq_span_only_checks_range(self):
        seed(6)
        result = verify((2, 4))
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 3)
        self.assertEqual(result["start"], 2)
        self.assertEqual(result["end"], 4)

    def test_seq_span_uses_entry_link(self):
        seed(6)
        chain._records[0]["record"]["action"] = "broken"
        result = verify((3, 5))
        self.assertTrue(result["ok"])

    def test_span_clamps_out_of_bounds(self):
        seed(3)
        result = verify((1, 99))
        self.assertEqual(result["end"], 2)

    def test_start_past_end_is_rejected(self):
        # 区间起点超出链尾不能静默变成“全链”或空区间
        seed(3)
        with self.assertRaises(ValueError):
            verify((10, 12))
        with self.assertRaises(ValueError):
            verify((2, 1))

    def test_day_span_crosses_days(self):
        seed(6)
        result = verify(("2026-01-01", "2026-01-03"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["start"], 0)
        self.assertEqual(result["end"], 5)

    def test_single_day_span(self):
        seed(6)
        result = verify("2026-01-02")
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["start"], 2)

    def test_dict_span(self):
        seed(6)
        result = verify({"start_day": "2026-01-02", "end_day": "2026-01-02"})
        self.assertEqual(result["checked"], 2)
        self.assertEqual(verify({"days": ["2026-01-01"]})["checked"], 2)

    def test_span_does_not_recompute_whole_chain(self):
        seed(10)
        calls = []
        original = chain._digest

        def counting(record, link="", algorithm=None, day=None, time=None, seq=None):
            calls.append(record)
            return original(record, link, algorithm, day, time, seq)

        chain._digest = counting
        try:
            verify((7, 8))
        finally:
            chain._digest = original
        self.assertEqual(len(calls), 2)

    def test_range_link_is_previous_digest(self):
        seed(5)
        result = verify((2, 3))
        self.assertEqual(result["link"], chain._records[1]["digest"])
        self.assertEqual(result["anchor"]["seq"], 1)

    def test_explicit_link_verifies_single_day(self):
        seed(4)
        prev = chain._records[1]["digest"]
        self.assertTrue(verify("2026-01-02", link=prev)["ok"])
        result = verify("2026-01-02", link="deadbeef")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "link")
        self.assertEqual(result["broken"], 2)

    def test_link_cannot_combine_with_resume(self):
        seed(3)
        patch = verify((0, 2), limit=1)
        with self.assertRaises(ValueError):
            verify(resume=patch["resume"], link="x")

    def test_invalid_span(self):
        with self.assertRaises(ValueError):
            verify((1, "2026-01-01"))
        with self.assertRaises(ValueError):
            verify((1, 2, 3))

    # --- 断点续算 -------------------------------------------------------

    def test_resume_after_limit(self):
        seed(6)
        first = verify((0, 5), limit=2)
        self.assertTrue(first["ok"])
        self.assertFalse(first["complete"])
        self.assertEqual(first["checked"], 2)
        self.assertEqual(first["resume"]["next"], 2)

        second = verify(resume=first["resume"], limit=2)
        self.assertEqual(second["checked"], 2)
        self.assertEqual(second["checked_total"], 4)

        third = verify(resume=second["resume"])
        self.assertTrue(third["ok"])
        self.assertTrue(third["complete"])
        self.assertEqual(third["checked_total"], 6)
        self.assertIsNone(third["resume"])

    def test_resume_detects_break_after_cut(self):
        seed(6)
        patch = verify((0, 5), limit=3)
        chain._records[4]["record"]["action"] = "broken"
        result = verify(resume=patch["resume"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 4)

    def test_resume_span_mismatch(self):
        seed(3)
        patch = verify((0, 2), limit=1)
        with self.assertRaises(ValueError):
            verify((0, 1), resume=patch["resume"])

    def test_resume_rejects_replaced_prefix(self):
        # 令牌绑定在已校验到的摘要上：前缀被整体替换后续算会拒绝，而不是接着冒充通过
        seed(4)
        patch = verify((0, 3), limit=2)
        chain._records[0]["digest"] = "0" * 64
        with self.assertRaises(ValueError):
            verify(resume=patch["resume"])

    def test_resume_rejects_tampered_boundary_record(self):
        # 断点前一条（已校验过的记录）在两次校验之间被改动
        seed(4)
        patch = verify((0, 3), limit=2)
        chain._records[1]["record"]["action"] = "tampered"
        with self.assertRaises(ValueError):
            verify(resume=patch["resume"])

    def test_resume_tolerates_new_appends(self):
        seed(3)
        patch = verify((0, 2), limit=1)
        append({"day": "2026-01-09", "action": "later", "time": "t9"})
        result = verify(resume=patch["resume"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["end"], 2)

    def test_resume_rejects_garbage(self):
        with self.assertRaises(ValueError):
            verify(resume={"v": 99})
        with self.assertRaises(ValueError):
            verify(resume="not-a-token")

    def test_limit_rejects_zero(self):
        with self.assertRaises(ValueError):
            verify(limit=0)

    def test_day_field_is_covered(self):
        seed(4)
        chain._records[2]["day"] = "2027-01-01"
        result = verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 2)

    def test_unknown_span_key_rejected(self):
        with self.assertRaises(ValueError):
            verify({"start": 0, "nope": 1})

    def test_historical_range_rerun_unchanged(self):
        # 后面的记录坏了，早先的区间单独重跑结果不变
        seed(6)
        before = verify(("2026-01-01", "2026-01-02"))
        chain._records[5]["record"]["action"] = "broken"
        after = verify(("2026-01-01", "2026-01-02"))
        self.assertTrue(after["ok"])
        self.assertEqual(
            {k: before[k] for k in ("checked", "start", "end", "digest")},
            {k: after[k] for k in ("checked", "start", "end", "digest")},
        )
        self.assertFalse(verify(("2026-01-02", "2026-01-03"))["ok"])

    def test_result_reports_days_and_anchor(self):
        seed(4)
        result = verify(("2026-01-01", "2026-01-02"))
        self.assertEqual(result["days"], ["2026-01-01", "2026-01-02"])
        self.assertTrue(result["found"])
        self.assertIsNone(result["anchor"])
        self.assertEqual(verify((1, 2))["anchor"]["seq"], 0)

    def test_missing_day_range_reports_not_found(self):
        seed(2)
        result = verify("2030-01-01")
        self.assertFalse(result["found"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 0)

    def test_days_and_tail_survive_missing_day(self):
        append(DAY1)
        append({"action": "no-day", "time": "t"})
        append(DAY2)
        self.assertEqual([d["day"] for d in days()], ["2026-01-01", "", "2026-01-02"])
        self.assertEqual(tail()["day"], "2026-01-02")

    # --- 算法迁移 -------------------------------------------------------

    def test_algorithm_recorded_per_entry(self):
        append(DAY1)
        set_algorithm("sha1")
        append(DAY2)
        self.assertEqual(chain._records[0]["algorithm"], "sha256")
        self.assertEqual(chain._records[1]["algorithm"], "sha1")
        self.assertTrue(verify()["ok"])

    def test_old_range_verifies_with_old_algorithm(self):
        seed(2)
        set_algorithm("sha1")
        seed(2)
        result = verify((0, 1))
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 2)

    def test_register_custom_algorithm(self):
        seed(2)

        def legacy(payload):
            import hashlib

            return hashlib.md5(payload).hexdigest()

        register_algorithm("legacy-md5", legacy)
        for i, entry in enumerate(chain._records):
            entry["algorithm"] = "legacy-md5"
            entry["link"] = chain._records[i - 1]["digest"] if i else ""
            entry["digest"] = chain._digest(
                entry["record"], entry["link"], "legacy-md5",
                entry["day"], entry["time"], entry["seq"],
            )
        self.assertTrue(verify()["ok"])
        # 默认算法仍是 sha256，新记录照常用它，旧记录按各自的算法校验
        self.assertEqual(get_algorithm(), "sha256")
        fresh = append(DAY1)
        self.assertEqual(fresh["algorithm"], "sha256")
        self.assertTrue(verify()["ok"])

    def test_unknown_algorithm(self):
        with self.assertRaises(ValueError):
            append(DAY1, algorithm="nope")

    # --- 查看 -----------------------------------------------------------

    def test_tail_returns_last_digest(self):
        append(DAY1)
        digest = append(DAY2)["digest"]
        self.assertEqual(tail()["digest"], digest)
        self.assertEqual(tail()["algorithm"], "sha256")

    def test_days_keeps_cross_day_link(self):
        seed(4)
        items = days()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1]["link"], items[0]["digest"])
        self.assertEqual(items[1]["first_seq"], 2)

    def test_boundary(self):
        seed(4)
        info = boundary("2026-01-02")
        self.assertEqual(info["first_seq"], 2)
        self.assertEqual(info["count"], 2)


if __name__ == "__main__":
    unittest.main()
