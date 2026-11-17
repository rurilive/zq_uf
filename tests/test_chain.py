import hashlib
import json
import unittest

from auditlog import (
    GENESIS,
    append,
    checked_ranges,
    days,
    register_algo,
    rerun,
    reset,
    set_algo,
    tail,
    verify,
    verify_day,
)
from auditlog import chain

DAY1 = {"day": "2026-01-01", "action": "login", "user": "alice"}
DAY2 = {"day": "2026-01-02", "action": "read", "user": "bob"}
DAY3 = {"day": "2026-01-03", "action": "logout", "user": "carol"}


class ChainTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_append_starts_at_zero(self):
        self.assertEqual(append(DAY1)["seq"], 0)

    def test_first_record_links_to_genesis(self):
        result = append(DAY1)
        self.assertEqual(result["link"], GENESIS)

    def test_append_links_to_previous_digest(self):
        first = append(DAY1)
        second = append(DAY2)
        self.assertEqual(second["link"], first["digest"])
        self.assertNotEqual(second["digest"], first["digest"])

    def test_cross_day_link_keeps_previous_digest(self):
        append(DAY1)
        append(DAY1)
        last_day1 = append(DAY1)
        first_day2 = append(DAY2)
        self.assertTrue(first_day2["day_start"])
        self.assertEqual(first_day2["link"], last_day1["digest"])
        self.assertEqual(first_day2["day_entry_link"], last_day1["digest"])
        self.assertEqual(days()[1]["entry_link"], last_day1["digest"])

    def test_same_content_gets_different_digest_by_position(self):
        # 相同内容出现在不同位置，摘要也必须不同（覆盖 seq 和 link）
        first = append(DAY1)
        second = append(dict(DAY1))
        self.assertNotEqual(first["digest"], second["digest"])
        self.assertNotEqual(first["link"], second["link"])

    def test_verify_detects_tamper(self):
        append(DAY1)
        append(DAY2)
        chain._records[0]["record"]["action"] = "deleted"
        result = verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 0)

    def test_verify_detects_link_tamper(self):
        append(DAY1)
        append(DAY2)
        chain._records[1]["link"] = "0" * 64
        result = verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 1)
        self.assertEqual(result["broken_detail"]["reason"], "link_mismatch")

    def test_verify_detects_reordered_records(self):
        for _ in range(3):
            append(DAY1)
        chain._records[0], chain._records[1] = (
            chain._records[1],
            chain._records[0],
        )
        result = verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 0)
        self.assertEqual(result["broken_detail"]["reason"], "seq_mismatch")

    def test_tail_returns_last_digest(self):
        append(DAY1)
        digest = append(DAY2)["digest"]
        self.assertEqual(tail()["digest"], digest)


class SpanTest(unittest.TestCase):
    def setUp(self):
        reset()
        for day in ("01", "01", "02", "02", "03"):
            append({"day": "2026-01-" + day, "action": "x"})

    def test_verify_only_checks_span(self):
        result = verify((1, 2))
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["start"], 1)
        self.assertEqual(result["end"], 2)
        self.assertEqual(result["range"], {"start": 1, "end": 2})

    def test_verify_single_seq(self):
        result = verify(3)
        self.assertEqual((result["start"], result["end"]), (3, 3))

    def test_verify_by_day_crosses_days(self):
        result = verify(("2026-01-01", "2026-01-03"))
        self.assertEqual(result["checked"], 5)
        self.assertEqual([part["day"] for part in result["days"]],
                         ["2026-01-01", "2026-01-02", "2026-01-03"])

    def test_verify_day_dict(self):
        result = verify({"from_day": "2026-01-02"})
        self.assertEqual((result["start"], result["end"]), (2, 4))

    def test_span_outside_data_is_empty(self):
        result = verify((10, 20))
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 0)
        self.assertIsNone(result["start"])

    def test_range_object(self):
        self.assertEqual(verify(range(1, 3))["checked"], 2)

    def test_anchor_allows_starting_mid_chain(self):
        # 只有拿到上一条摘要的人才能从中间开始验证
        records = list(chain._records)
        expected = records[2]["digest"]
        self.assertTrue(verify((3, 4))["ok"])
        self.assertFalse(verify((3, 4), anchor="bad")["ok"])
        self.assertTrue(verify((3, 4), anchor=expected)["ok"])

    def test_days_lists_segments(self):
        parts = days()
        self.assertEqual(len(parts), 3)
        self.assertEqual((parts[0]["start"], parts[0]["end"]), (0, 1))
        self.assertEqual(parts[0]["exit_digest"], chain._records[1]["digest"])
        self.assertEqual(parts[1]["entry_link"], chain._records[1]["digest"])
        self.assertEqual(parts[1]["prev_day"], "2026-01-01")


class ResumeTest(unittest.TestCase):
    def setUp(self):
        reset()
        for day in ("01", "01", "02", "02", "03"):
            append({"day": "2026-01-" + day, "action": "x"})

    def test_resume_after_interruption(self):
        first = verify((0, 4), limit=2)
        self.assertFalse(first["done"])
        self.assertTrue(first["ok"])
        self.assertEqual(first["resume"], 2)
        self.assertEqual(first["link"], chain._records[1]["digest"])

        second = verify((0, 4), resume=first["resume"], limit=2)
        self.assertEqual((second["start"], second["end"]), (2, 3))
        self.assertEqual(second["resume"], 4)

        third = verify((0, 4), resume=second["resume"])
        self.assertTrue(third["done"])
        self.assertEqual((third["start"], third["end"]), (4, 4))
        self.assertIsNone(third["resume"])

    def test_resume_keeps_link_continuity(self):
        first = verify((0, 4), limit=1)
        second = verify((0, 4), resume=first["resume"], limit=1)
        self.assertEqual(second["anchor"], first["link"])

    def test_broken_chain_stops_and_reports(self):
        chain._records[3]["record"]["action"] = "deleted"
        result = verify((0, 4), limit=2)
        self.assertTrue(result["ok"])  # 本次只校验到 0..1
        self.assertEqual(result["resume"], 2)
        result = verify((0, 4), resume=result["resume"], limit=1)
        self.assertTrue(result["ok"])
        result = verify((0, 4), resume=result["resume"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 3)
        self.assertEqual(result["broken_range"], {"start": 3, "end": 3})
        detail = result["broken_detail"]
        self.assertEqual(detail["seq"], 3)
        self.assertEqual(detail["time"], "2026-01-02")
        self.assertEqual(detail["prev"]["seq"], 2)
        self.assertEqual(detail["next"]["seq"], 4)
        self.assertEqual(result["impact"]["start"], 3)
        self.assertEqual(result["impact"]["count"], 2)
        self.assertEqual(result["impact"]["days"],
                         ["2026-01-02", "2026-01-03"])
        self.assertGreaterEqual(detail["computed_digest"], "")

    def test_cross_day_break_reports_neighbors(self):
        # 跨天衔接值被改：断在第 3 条（02 天的第一条）
        chain._records[2]["link"] = "0" * 64
        result = verify()
        self.assertFalse(result["ok"])
        detail = result["broken_detail"]
        self.assertEqual(detail["seq"], 2)
        self.assertEqual(detail["prev"]["seq"], 1)
        self.assertEqual(detail["prev"]["day"], "2026-01-01")
        self.assertEqual(detail["next"]["day"], "2026-01-02")
        self.assertEqual(detail["expected_link"],
                         result["broken_detail"]["prev"]["digest"])

    def test_day_link_tamper_detected(self):
        chain._records[2]["day_entry_link"] = "0" * 64
        result = verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_detail"]["reason"],
                         "day_link_mismatch")

    def test_limit_must_be_positive(self):
        with self.assertRaises(ValueError):
            verify((0, 2), limit=0)

    def test_resume_beyond_range_is_done(self):
        result = verify((0, 1), resume=2)
        self.assertTrue(result["done"])
        self.assertEqual(result["checked"], 0)

    def test_span_anchor_defaults_to_previous_digest(self):
        result = verify((2, 4))
        self.assertEqual(result["anchor"], chain._records[1]["digest"])

    def test_checked_ranges_recorded_and_rerun(self):
        verify((0, 1))
        verify((2, 3))
        ranges = checked_ranges()
        self.assertEqual(len(ranges), 2)
        results = rerun()
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item["ok"] for item in results))
        # 单独重跑历史区间
        self.assertEqual(rerun((2, 3))["checked"], 2)


class TimeSpanTest(unittest.TestCase):
    def setUp(self):
        reset()
        for hour in ("08:00:00", "12:00:00", "18:00:00"):
            append({"day": "2026-02-01", "ts": "2026-02-01T" + hour,
                    "action": "x"})
        for hour in ("08:00:00", "20:00:00"):
            append({"day": "2026-02-02", "ts": "2026-02-02T" + hour,
                    "action": "x"})

    def test_time_window_within_one_day(self):
        result = verify({"from_time": "2026-02-01T09:00:00",
                         "to_time": "2026-02-01T18:00:00"})
        self.assertEqual((result["start"], result["end"]), (1, 2))
        self.assertEqual(result["checked"], 2)

    def test_time_window_crosses_days(self):
        result = verify({"from_time": "2026-02-01T12:00:00",
                         "to_time": "2026-02-02T09:00:00"})
        self.assertEqual((result["start"], result["end"]), (1, 3))
        self.assertEqual([part["day"] for part in result["days"]],
                         ["2026-02-01", "2026-02-02"])

    def test_full_timestamp_string(self):
        result = verify(("2026-02-01T18:00:00", "2026-02-02T20:00:00"))
        self.assertEqual((result["start"], result["end"]), (2, 4))

    def test_day_string_still_whole_day(self):
        self.assertEqual(verify("2026-02-02")["checked"], 2)

    def test_from_stamp(self):
        result = verify({"from_stamp": "2026-02-02T00:00:00"})
        self.assertEqual((result["start"], result["end"]), (3, 4))


class SingleDayTest(unittest.TestCase):
    def setUp(self):
        reset()
        for hour in ("08:00:00", "12:00:00"):
            append({"day": "2026-03-01", "ts": "2026-03-01T" + hour,
                    "action": "x"})
        for hour in ("08:00:00", "20:00:00"):
            append({"day": "2026-03-02", "ts": "2026-03-02T" + hour,
                    "action": "x"})

    def test_verify_one_day_alone(self):
        result = verify_day("2026-03-02")
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 2)
        self.assertEqual((result["start"], result["end"]), (2, 3))
        self.assertEqual(result["anchor"], chain._records[1]["digest"])

    def test_verify_day_detects_tamper_in_day(self):
        chain._records[3]["record"]["action"] = "deleted"
        result = verify_day("2026-03-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 3)

    def test_verify_day_with_trusted_entry_link(self):
        self.assertTrue(verify_day("2026-03-02")["ok"])
        self.assertFalse(
            verify_day("2026-03-02", entry_link="0" * 64)["ok"]
        )

    def test_verify_day_resume(self):
        first = verify_day("2026-03-02", limit=1)
        self.assertEqual(first["resume"], 3)
        second = verify_day("2026-03-02", resume=first["resume"])
        self.assertTrue(second["done"])
        self.assertEqual(second["checked"], 1)

    def test_verify_unknown_day_is_empty(self):
        result = verify_day("2026-04-01")
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 0)


class TruncationTest(unittest.TestCase):
    def setUp(self):
        reset()
        for day in ("01", "01", "02", "02"):
            append({"day": "2026-05-" + day, "ts": "2026-05-" + day + "T08:00",
                    "action": "x"})

    def test_deleted_middle_record_detected(self):
        del chain._records[1]
        result = verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 1)
        self.assertIn(result["broken_detail"]["reason"],
                      ("seq_mismatch", "link_mismatch", "digest_mismatch"))

    def test_tail_truncation_needs_external_anchor(self):
        # 直接删掉尾部时链本身仍自洽，必须用外部保存的摘要才看得出来
        expected_tail = tail()["digest"]
        count = len(chain._records)
        del chain._records[-1]
        self.assertTrue(verify()["ok"])
        result = verify((0, count - 1), expect_tail=expected_tail)
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_detail"]["reason"], "truncated")
        self.assertEqual(result["broken"], count - 1)

    def test_whole_range_deleted_with_external_tail(self):
        expected_tail = tail()["digest"]
        chain._records.clear()
        self.assertTrue(verify()["ok"])
        result = verify((0, 3), expect_tail=expected_tail)
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_detail"]["reason"], "truncated")
        self.assertEqual(result["impact"]["kind"], "truncated")
        # 断链结果也要留档，便于之后单独重跑
        self.assertEqual(
            [item["broken"] for item in checked_ranges()], [0]
        )

    def test_verify_day_detects_deleted_day_tail(self):
        part = [item for item in days() if item["day"] == "2026-05-02"][0]
        expected_tail = part["exit_digest"]
        del chain._records[3]
        result = verify_day("2026-05-02", entry_link=part["entry_link"],
                            expect_tail=expected_tail)
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_detail"]["reason"], "truncated")


class AlgoTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_algo_change_keeps_old_links_verifiable(self):
        old = append(DAY1)
        set_algo("sha3_256")
        new = append(DAY2)
        self.assertEqual(new["link"], old["digest"])
        self.assertEqual(new["algo"], "sha3_256")
        result = verify()
        self.assertTrue(result["ok"])
        self.assertEqual(result["algos"], ["sha256", "sha3_256"])

    def test_custom_algo(self):
        register_algo("md5x", hashlib.md5)
        append(DAY1)
        set_algo("md5x")
        result = append(DAY2)
        self.assertEqual(result["algo"], "md5x")
        self.assertEqual(len(result["digest"]), 32)
        self.assertTrue(verify()["ok"])
        self.assertEqual(verify()["algos"], ["md5x", "sha256"])

    def test_unknown_algo_rejected(self):
        with self.assertRaises(ValueError):
            append(DAY1, algo="nope")

    def test_legacy_records_still_verify(self):
        # 模拟改造前的快照：没有 algo/link，摘要只覆盖自身内容
        payload = json.dumps(DAY1, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        chain._records.append({
            "seq": 0, "day": DAY1["day"], "record": dict(DAY1),
            "digest": digest,
        })
        result = verify()
        self.assertTrue(result["ok"])
        self.assertEqual(result["algos"], [chain.LEGACY_ALGO])
        chain._records[0]["record"]["user"] = "mallory"
        result = verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 0)


if __name__ == "__main__":
    unittest.main()
