import unittest

from sqldetect import (
    DB_SIGNATURES,
    check,
    load,
    mark_false_positive,
    match_error_signatures,
    report,
    reset,
)

URL = "http://app.local/item"
OTHER = "http://app.local/profile"

MYSQL_ERROR = "You have an error in your SQL syntax; check the manual that corresponds to your MySQL server version"
PG_ERROR = 'pg::syntaxerror: ERROR: syntax error at or near "\'"'
MSSQL_ERROR = "Microsoft OLE DB Provider for SQL Server error '80040e14' Unclosed quotation mark"
ORA_ERROR = "ORA-00933: SQL command not properly ended"
SQLITE_ERROR = "SQLite3::SQLException: unrecognized token: \"'\""
NORMAL_PAGE = "<html><body>welcome back, list of 12 items</body></html>"
EMPTY_PAGE = "<html><body>no results found</body></html>"


class IsolatedTestCase(unittest.TestCase):
    def setUp(self):
        reset()


class SignatureTest(IsolatedTestCase):
    def test_dbms_signatures_are_separated(self):
        self.assertEqual(match_error_signatures(MYSQL_ERROR)[0]["dbms"], "mysql")
        self.assertEqual(match_error_signatures(PG_ERROR)[0]["dbms"], "postgresql")
        self.assertEqual(match_error_signatures(MSSQL_ERROR)[0]["dbms"], "mssql")
        self.assertEqual(match_error_signatures(ORA_ERROR)[0]["dbms"], "oracle")
        self.assertEqual(match_error_signatures(SQLITE_ERROR)[0]["dbms"], "sqlite")

    def test_unknown_text_matches_nothing(self):
        self.assertEqual(match_error_signatures("welcome back"), [])
        self.assertEqual(match_error_signatures(""), [])

    def test_short_oracle_signature_requires_word_boundary(self):
        self.assertEqual(match_error_signatures("aurora-borealis tour"), [])
        self.assertEqual(match_error_signatures("FLORA-2000 product"), [])
        self.assertEqual(match_error_signatures(ORA_ERROR)[0]["dbms"], "oracle")

    def test_signature_tables_cover_named_dbms(self):
        for dbms in ("mysql", "postgresql", "mssql", "oracle", "sqlite"):
            self.assertIn(dbms, DB_SIGNATURES)


class ErrorInjectionTest(IsolatedTestCase):
    def test_error_finding_reports_dbms_and_evidence(self):
        load(URL, {"id": [{"payload": "'", "body": MYSQL_ERROR}]})
        out = check(URL, ["id"])
        self.assertEqual(len(out["findings"]), 1)
        item = out["findings"][0]
        self.assertEqual(item["param"], "id")
        self.assertEqual(item["kind"], "error")
        self.assertEqual(item["dbms"], "mysql")
        self.assertEqual(item["status"], "confirmed")
        self.assertIn("mysql", item["evidence"])
        self.assertIn("id=", item["request"])
        self.assertTrue(item["steps"])
        self.assertTrue(item["normal_page"] is not None or item["baseline"] is None)
        self.assertEqual(item["abnormal_page"], MYSQL_ERROR)

    def test_same_param_many_payloads_one_conclusion(self):
        load(URL, {"id": [
            {"payload": "'", "body": MYSQL_ERROR},
            {"payload": '"', "body": MYSQL_ERROR},
            {"payload": "1'", "body": MYSQL_ERROR},
        ]})
        out = check(URL, ["id"])
        self.assertEqual(len(out["findings"]), 1)
        self.assertEqual(out["duplicates"], 0)

    def test_dbms_not_confused_across_types(self):
        load(URL, {"a": [{"payload": "'", "body": PG_ERROR}],
                   "b": [{"payload": "'", "body": MSSQL_ERROR}]})
        out = check(URL, ["a", "b"])
        by_param = {item["param"]: item for item in out["findings"]}
        self.assertEqual(by_param["a"]["dbms"], "postgresql")
        self.assertEqual(by_param["b"]["dbms"], "mssql")

    def test_baseline_error_is_not_injection(self):
        load(URL, {"page": {
            "baseline": {"body": MYSQL_ERROR},
            "true": {"payload": "' OR '1'='1", "body": MYSQL_ERROR},
        }})
        out = check(URL, ["page"])
        self.assertEqual(out["findings"], [])

    def test_baseline_match_does_not_hide_other_dbms(self):
        # 基线里的 MySQL 特征应被剔除，但同一次响应里的 PostgreSQL 特征不能一起丢。
        load(URL, {"id": {
            "baseline": {"body": MYSQL_ERROR},
            "'": {"body": MYSQL_ERROR + " " + PG_ERROR},
        }})
        item = check(URL, ["id"])["findings"][0]
        self.assertEqual(item["kind"], "error")
        self.assertEqual(item["dbms"], "postgresql")


class BooleanBlindTest(IsolatedTestCase):
    def test_true_false_response_difference_detected(self):
        load(URL, {"name": {
            "baseline": {"body": NORMAL_PAGE, "elapsed": 0.1},
            "true": {"payload": "' OR '1'='1", "body": NORMAL_PAGE, "elapsed": 0.1},
            "false": {"payload": "' OR '1'='2", "body": EMPTY_PAGE, "elapsed": 0.1},
        }})
        out = check(URL, ["name"])
        self.assertEqual(len(out["findings"]), 1)
        item = out["findings"][0]
        self.assertEqual(item["kind"], "boolean")
        self.assertEqual(item["status"], "confirmed")
        self.assertIn("布尔盲注", item["evidence"])
        self.assertEqual(item["evidence_detail"]["true_payload"], "' OR '1'='1")
        self.assertEqual(item["evidence_detail"]["false_payload"], "' OR '1'='2")
        self.assertEqual(item["normal_page"], NORMAL_PAGE)
        self.assertEqual(item["abnormal_page"], EMPTY_PAGE)

    def test_identical_true_false_not_detected(self):
        load(URL, {"name": {
            "baseline": {"body": NORMAL_PAGE},
            "true": {"payload": "' OR '1'='1", "body": NORMAL_PAGE},
            "false": {"payload": "' OR '1'='2", "body": NORMAL_PAGE},
        }})
        self.assertEqual(check(URL, ["name"])["findings"], [])

    def test_difference_without_baseline_is_confirmed_at_lower_confidence(self):
        load(URL, {"name": {
            "true": {"payload": "' OR '1'='1", "body": NORMAL_PAGE},
            "false": {"payload": "' OR '1'='2", "body": EMPTY_PAGE},
        }})
        out = check(URL, ["name"])
        self.assertEqual(len(out["findings"]), 1)
        self.assertEqual(out["findings"][0]["kind"], "boolean")
        # 真/假对照本身就是布尔盲注的判据，没有基线也可以确认，但置信度更低。
        self.assertEqual(out["findings"][0]["status"], "confirmed")
        self.assertLess(out["findings"][0]["confidence"], 0.8)
        self.assertEqual(out["pending"], [])

    def test_normal_page_not_flagged(self):
        load(URL, {"name": {"baseline": {"body": NORMAL_PAGE},
                            "true": {"payload": "' OR '1'='1", "body": NORMAL_PAGE}}})
        self.assertEqual(check(URL, ["name"])["findings"], [])

    def test_localised_difference_on_large_page_detected(self):
        boilerplate = "<nav><a href='/'>home</a></nav>" * 30
        normal = boilerplate + "<ul><li>item1</li><li>item2</li></ul>"
        empty = boilerplate + "<p>No matches.</p>"
        load(URL, {"q": {"baseline": {"body": normal},
                         "true": {"payload": "' OR '1'='1", "body": normal},
                         "false": {"payload": "' OR '1'='2", "body": empty}}})
        item = check(URL, ["q"])["findings"][0]
        self.assertEqual(item["kind"], "boolean")
        self.assertEqual(item["status"], "confirmed")
        self.assertGreaterEqual(item["evidence_detail"]["changed_chars"], 8)

    def test_single_token_noise_on_large_page_not_flagged(self):
        boilerplate = "<nav><a href='/'>home</a></nav>" * 30
        normal = boilerplate + "<ul><li>item1</li><li>item2</li></ul>"
        noisy = boilerplate + "<ul><li>item1</li><li>item3</li></ul>"
        load(URL, {"q": {"baseline": {"body": normal},
                         "true": {"payload": "' OR '1'='1", "body": normal},
                         "false": {"payload": "' OR '1'='2", "body": noisy}}})
        self.assertEqual(check(URL, ["q"])["findings"], [])

    def test_strongest_pair_wins_when_multiple_registered(self):
        # 第一组真/假响应无差异，第二组有差异：不能只看第一组而漏报。
        load(URL, {"trials": {"baseline": {"body": NORMAL_PAGE},
                              "trials": [
                                  {"payload": "1 AND 1=1", "body": NORMAL_PAGE},
                                  {"payload": "1 AND 1=2", "body": NORMAL_PAGE},
                                  {"payload": "' OR '1'='1", "body": NORMAL_PAGE},
                                  {"payload": "' OR '1'='2", "body": EMPTY_PAGE},
                              ]}})
        item = check(URL, ["trials"])["findings"][0]
        self.assertEqual(item["kind"], "boolean")
        self.assertEqual(item["status"], "confirmed")
        self.assertEqual(item["evidence_detail"]["false_payload"], "' OR '1'='2")


class TimeBlindTest(IsolatedTestCase):
    def test_elapsed_difference_detected(self):
        load(URL, {"sort": {
            "baseline": {"body": NORMAL_PAGE, "elapsed": 0.2},
            "timed": {"payload": "1' AND SLEEP(5)--", "body": NORMAL_PAGE, "elapsed": 5.4},
        }})
        out = check(URL, ["sort"])
        self.assertEqual(len(out["findings"]), 1)
        item = out["findings"][0]
        self.assertEqual(item["kind"], "time")
        self.assertEqual(item["status"], "confirmed")
        self.assertIn("时间盲注", item["evidence"])
        self.assertGreater(item["evidence_detail"]["delta_seconds"], 1.0)

    def test_close_timings_not_detected(self):
        load(URL, {"sort": {
            "baseline": {"body": NORMAL_PAGE, "elapsed": 0.2},
            "timed": {"payload": "1' AND SLEEP(5)--", "body": NORMAL_PAGE, "elapsed": 0.25},
        }})
        self.assertEqual(check(URL, ["sort"])["findings"], [])

    def test_small_delay_is_pending(self):
        load(URL, {"sort": {
            "baseline": {"body": NORMAL_PAGE, "elapsed": 0.2},
            "timed": {"payload": "1' AND SLEEP(1)--", "body": NORMAL_PAGE, "elapsed": 1.0},
        }})
        out = check(URL, ["sort"])
        self.assertEqual(out["findings"][0]["kind"], "time")
        self.assertEqual(out["findings"][0]["status"], "pending")

    def test_millisecond_timings_are_normalised(self):
        load(URL, {"sort": {
            "baseline": {"body": NORMAL_PAGE, "ms": 200},
            "timed": {"payload": "1' AND SLEEP(5)--", "body": NORMAL_PAGE, "ms": 5400},
        }})
        item = check(URL, ["sort"])["findings"][0]
        self.assertEqual(item["kind"], "time")
        self.assertEqual(item["evidence_detail"]["baseline_seconds"], 0.2)

    def test_multiple_timed_samples_under_one_role(self):
        load(URL, {"sort": {
            "baseline": {"body": NORMAL_PAGE, "elapsed": 0.2},
            "timed": [{"payload": "SLEEP(5)", "body": NORMAL_PAGE, "elapsed": 5.1},
                      {"payload": "SLEEP(5)", "body": NORMAL_PAGE, "elapsed": 5.2}],
        }})
        item = check(URL, ["sort"])["findings"][0]
        self.assertEqual(item["kind"], "time")
        self.assertGreaterEqual(item["confidence"], 0.9)

    def test_baseline_median_excludes_slow_sample(self):
        # 没有显式 baseline 时，基线不能把慢样本自己算进去。
        load(URL, {"sort": [("a", NORMAL_PAGE, 0.2), ("b", NORMAL_PAGE, 5.2)]})
        item = check(URL, ["sort"])["findings"][0]
        self.assertEqual(item["kind"], "time")
        self.assertEqual(item["status"], "confirmed")
        self.assertEqual(item["evidence_detail"]["baseline_seconds"], 0.2)
        self.assertAlmostEqual(item["evidence_detail"]["delta_seconds"], 5.0, places=3)

    def test_normal_page_uses_declared_baseline(self):
        load(URL, {"sort": {
            "true": {"payload": "t", "body": "TRUEBODY"},
            "baseline": {"body": "BASEBODY", "elapsed": 0.2},
            "timed": {"payload": "SLEEP(5)", "body": "TIMEDBODY", "elapsed": 5.2},
        }})
        item = check(URL, ["sort"])["findings"][0]
        self.assertEqual(item["normal_page"], "BASEBODY")
        self.assertEqual(item["abnormal_page"], "TIMEDBODY")

    def test_no_baseline_reports_none_not_zero(self):
        load(URL, {"sort": {"timed": {"payload": "SLEEP(5)", "body": "x", "elapsed": 5.2}}})
        item = check(URL, ["sort"])["findings"][0]
        self.assertIsNone(item["evidence_detail"]["baseline_seconds"])
        self.assertFalse(item["evidence_detail"]["baseline_present"])


class ConflictTest(IsolatedTestCase):
    def test_strongest_evidence_wins(self):
        # 同一条参数既有报错、又有布尔差异：报错特征更具体，取报错。
        load(URL, {"id": {
            "baseline": {"body": NORMAL_PAGE},
            "true": {"payload": "' OR '1'='1", "body": NORMAL_PAGE},
            "false": {"payload": "' OR '1'='2", "body": EMPTY_PAGE},
            "error": {"payload": "'", "body": MYSQL_ERROR},
        }})
        out = check(URL, ["id"])
        self.assertEqual(len(out["findings"]), 1)
        self.assertEqual(out["findings"][0]["kind"], "error")
        kinds = [alt["kind"] for alt in out["findings"][0]["alternatives"]]
        self.assertIn("boolean", kinds)

    def test_error_beats_time_and_alternatives_sorted(self):
        load(URL, {"id": {
            "baseline": {"body": NORMAL_PAGE, "elapsed": 0.2},
            "true": {"payload": "' OR '1'='1", "body": NORMAL_PAGE, "elapsed": 0.2},
            "false": {"payload": "' OR '1'='2", "body": EMPTY_PAGE, "elapsed": 0.2},
            "timed": {"payload": "1' AND SLEEP(5)--", "body": NORMAL_PAGE, "elapsed": 6.0},
            "err": {"payload": "'", "body": MYSQL_ERROR},
        }})
        winner = check(URL, ["id"])["findings"][0]
        self.assertEqual(winner["kind"], "error")
        confidences = [alt["confidence"] for alt in winner["alternatives"]]
        self.assertEqual(confidences, sorted(confidences, reverse=True))
        self.assertIn("time", [alt["kind"] for alt in winner["alternatives"]])

    def test_unknown_role_key_is_not_dropped(self):
        # 认不出的键不能丢，否则会漏掉该键里的报错证据。
        load(URL, {"id": {
            "true": {"payload": "' OR '1'='1", "body": NORMAL_PAGE},
            "false": {"payload": "' OR '1'='2", "body": EMPTY_PAGE},
            "weird": {"payload": "'", "body": MYSQL_ERROR},
        }})
        winner = check(URL, ["id"])["findings"][0]
        self.assertEqual(winner["kind"], "error")

    def test_weak_boolean_loses_to_strong_time(self):
        load(URL, {"id": {
            "baseline": {"body": NORMAL_PAGE, "elapsed": 0.2},
            "true": {"payload": "' OR '1'='1", "body": NORMAL_PAGE, "elapsed": 0.2},
            "false": {"payload": "' OR '1'='2", "body": EMPTY_PAGE, "elapsed": 0.2},
            "timed": {"payload": "1' AND SLEEP(5)--", "body": NORMAL_PAGE, "elapsed": 6.0},
        }})
        winner = check(URL, ["id"])["findings"][0]
        self.assertIn(winner["kind"], ("boolean", "time"))
        self.assertFalse([alt for alt in winner["alternatives"]
                          if alt["confidence"] > winner["confidence"]])

    def test_confirmed_beats_pending_regardless_of_confidence(self):
        # 待确认的布尔（置信度较高）不应压过已确认的时间盲注。
        load(URL, {"id": {
            "baseline": {"body": "AAAA", "elapsed": 0.2},
            "true": {"payload": "' OR '1'='1", "body": "AAAA", "elapsed": 0.2},
            "false": {"payload": "' OR '1'='2", "body": "AAAABBBB", "elapsed": 0.2},
            "timed": {"payload": "SLEEP(5)", "body": "AAAA", "elapsed": 5.2},
        }})
        winner = check(URL, ["id"])["findings"][0]
        self.assertEqual(winner["status"], "confirmed")
        self.assertEqual(winner["kind"], "time")

    def test_winner_alternatives_sorted_descending(self):
        load(URL, {"id": {
            "baseline": {"body": "ok page", "elapsed": 0.2},
            "true": {"payload": "' OR '1'='1", "body": "ok page", "elapsed": 0.2},
            "false": {"payload": "' OR '1'='2", "body": "nope page", "elapsed": 0.2},
            "timed": {"payload": "SLEEP(5)", "body": "ok page", "elapsed": 6.0},
            "err": {"payload": "'", "body": MYSQL_ERROR},
        }})
        winner = check(URL, ["id"])["findings"][0]
        confidences = [alt["confidence"] for alt in winner["alternatives"]]
        self.assertEqual(confidences, sorted(confidences, reverse=True))


class UncoveredTest(IsolatedTestCase):
    def test_skip_reasons_are_categorised(self):
        load(URL, {
            "slow": {"skip": "timeout"},
            "admin": {"status": 403, "body": "Forbidden"},
            "hook": {"skip": "协议不支持：ftp:// scheme"},
            "gone": {"error": "connection refused"},
        })
        out = check(URL, ["slow", "admin", "hook", "gone"])
        reasons = {item["param"]: item["reason"] for item in out["uncovered"]}
        self.assertEqual(reasons, {
            "slow": "超时",
            "admin": "拒绝",
            "hook": "协议不支持",
            "gone": "拒绝",
        })
        self.assertEqual(out["findings"], [])
        self.assertEqual(set(out["pending"]), set())

    def test_reason_codes_are_machine_readable(self):
        load(URL, {"slow": {"skip": "timeout"}})
        entry = check(URL, ["slow"])["uncovered"][0]
        self.assertEqual(entry["reason_code"], "timeout")

    def test_unknown_param_is_uncovered(self):
        load(URL, {"id": "welcome"})
        out = check(URL, ["id", "nope"])
        self.assertIn("nope", [item["param"] for item in out["uncovered"]])

    def test_not_requested_params_listed(self):
        load(URL, {"id": "welcome", "extra": "ok"})
        out = check(URL, ["id"])
        self.assertEqual(out["not_requested"], ["extra"])


class ReportTest(IsolatedTestCase):
    def test_per_interface_conclusions(self):
        load(URL, {"id": [{"payload": "'", "body": MYSQL_ERROR}],
                   "name": {"true": {"payload": "' OR '1'='1", "body": NORMAL_PAGE},
                            "false": {"payload": "' OR '1'='2", "body": EMPTY_PAGE}}})
        check(URL, ["id", "name"])
        rep = report(URL)
        self.assertEqual(rep["findings"], 2)
        self.assertEqual(rep["confirmed"], 2)
        self.assertEqual(rep["pending"], 0)
        self.assertEqual(rep["duplicates"], 0)
        self.assertEqual(rep["params"], {"id": 1, "name": 1})
        self.assertEqual(len(rep["conclusions"]), 2)

    def test_pending_counted_separately(self):
        load(URL, {"q": "SQL syntax issue near input"})
        check(URL, ["q"])
        rep = report(URL)
        self.assertEqual(rep["findings"], 1)
        self.assertEqual(rep["confirmed"], 0)
        self.assertEqual(rep["pending"], 1)
        self.assertEqual(rep["pending_params"], ["q"])

    def test_global_report_covers_all_interfaces(self):
        load(URL, {"id": [{"payload": "'", "body": MYSQL_ERROR}]})
        load(OTHER, {"q": PG_ERROR})
        check(URL, ["id"])
        check(OTHER, ["q"])
        rep = report()
        self.assertEqual(rep["findings"], 2)
        self.assertEqual(set(rep["interfaces"]), {URL, OTHER})
        self.assertEqual(rep["uncovered_count"], 0)
        self.assertEqual(rep["uncovered"], [])

    def test_report_before_check_does_not_crash(self):
        load(URL, {"id": "welcome"})
        rep = report()
        self.assertEqual(rep["findings"], 0)


class FalsePositiveTest(IsolatedTestCase):
    def setUp(self):
        reset()
        load(URL, {"id": [{"payload": "'", "body": MYSQL_ERROR}],
                   "name": [{"payload": "'", "body": PG_ERROR}]})
        check(URL, ["id", "name"])

    def test_mark_with_reason(self):
        marked = mark_false_positive(URL, "id", "页面模板本身带 SQL 文案")
        self.assertTrue(marked["false_positive"])
        self.assertEqual(marked["status"], "false_positive")
        self.assertEqual(marked["reason"], "页面模板本身带 SQL 文案")
        rep = report(URL)
        self.assertEqual(rep["confirmed"], 1)
        self.assertEqual(rep["false_positives"], 1)
        self.assertEqual(rep["false_positive_details"][0]["reason"], "页面模板本身带 SQL 文案")

    def test_mark_requires_reason(self):
        with self.assertRaises(ValueError):
            mark_false_positive(URL, "id", "   ")

    def test_mark_unknown_param_rejected(self):
        with self.assertRaises(ValueError):
            mark_false_positive(URL, "nope", "原因")

    def test_reload_clears_false_positive(self):
        mark_false_positive(URL, "id", "误报")
        load(URL, {"id": [{"payload": "'", "body": MYSQL_ERROR}]})
        check(URL, ["id"])
        self.assertFalse(report(URL)["conclusions"][0]["false_positive"])

    def test_three_arg_form_with_none_reason_reports_reason_error(self):
        with self.assertRaises(ValueError) as ctx:
            mark_false_positive(URL, "id", None)
        self.assertIn("原因", str(ctx.exception))


class IsolationTest(IsolatedTestCase):
    def test_mutating_returned_finding_does_not_corrupt_state(self):
        load(URL, {"id": [{"payload": "'", "body": MYSQL_ERROR}]})
        out = check(URL, ["id"])
        out["findings"][0]["evidence_detail"]["dbms"] = "tampered"
        out["findings"][0]["steps"].append("tampered")
        rep = report(URL)
        self.assertEqual(rep["conclusions"][0]["evidence_detail"]["dbms"], "mysql")
        self.assertNotIn("tampered", rep["conclusions"][0]["steps"])

    def test_mutating_input_after_load_does_not_corrupt_state(self):
        nested = [{"payload": "'", "body": MYSQL_ERROR}]
        load(URL, {"id": nested})
        nested.append({"payload": '"', "body": "clean"})
        nested[0]["body"] = "clean"
        self.assertEqual(check(URL, ["id"])["findings"][0]["dbms"], "mysql")

    def test_repeated_check_does_not_inflate_attempts(self):
        load(URL, {"id": [{"payload": "'", "body": MYSQL_ERROR}]})
        for _ in range(3):
            check(URL, ["id"])
        self.assertEqual(report(URL)["attempts"], 1)


class ReportShapeTest(IsolatedTestCase):
    def test_per_url_and_global_uncovered_are_both_lists(self):
        load(URL, {"a": "x", "slow": {"skip": "timeout"}})
        check(URL, ["a", "slow"])
        self.assertIsInstance(report(URL)["uncovered"], list)
        self.assertIsInstance(report()["uncovered"], list)
        self.assertEqual(report(URL)["uncovered_count"], 1)
        self.assertEqual(report()["uncovered_count"], 1)


class InputFormTest(IsolatedTestCase):
    def test_plain_string_still_works(self):
        load(URL, {"id": MYSQL_ERROR})
        self.assertEqual(check(URL, ["id"])["findings"][0]["dbms"], "mysql")

    def test_tuple_form_carries_elapsed(self):
        load(URL, {"sort": [
            ("1", NORMAL_PAGE, 0.2),
            ("1' AND SLEEP(5)--", NORMAL_PAGE, 5.2),
        ]})
        item = check(URL, ["sort"])["findings"][0]
        self.assertEqual(item["kind"], "time")

    def test_duplicate_params_collapse(self):
        load(URL, {"id": [{"payload": "'", "body": MYSQL_ERROR}]})
        out = check(URL, ["id", "id", "id"])
        self.assertEqual(len(out["findings"]), 1)

    def test_reject_empty_params(self):
        load(URL, {"id": "x"})
        with self.assertRaises(ValueError):
            check(URL, [])

    def test_reject_unloaded_url(self):
        with self.assertRaises(ValueError):
            check("http://nope.local", ["id"])

    def test_skip_false_means_not_skipped(self):
        load(URL, {"id": {"skip": False, "body": MYSQL_ERROR}})
        self.assertEqual(check(URL, ["id"])["findings"][0]["dbms"], "mysql")

    def test_error_field_with_db_text_treated_as_abnormal_page(self):
        load(URL, {"id": {"error": ORA_ERROR}})
        item = check(URL, ["id"])["findings"][0]
        self.assertEqual(item["kind"], "error")
        self.assertEqual(item["dbms"], "oracle")

    def test_transport_error_is_skip_not_finding(self):
        load(URL, {"id": {"error": "connection refused"}})
        out = check(URL, ["id"])
        self.assertEqual(out["findings"], [])
        self.assertEqual(out["uncovered"][0]["reason"], "拒绝")

    def test_list_error_only_trial_not_lost(self):
        load(URL, {"id": [{"error": ORA_ERROR}]})
        item = check(URL, ["id"])["findings"][0]
        self.assertEqual(item["kind"], "error")
        self.assertEqual(item["dbms"], "oracle")

    def test_list_with_transport_error_dropped_from_trials(self):
        load(URL, {"id": [{"body": NORMAL_PAGE},
                          {"error": "connection refused"}]})
        self.assertEqual(check(URL, ["id"])["findings"], [])


class PerformanceTest(IsolatedTestCase):
    def test_large_pages_stay_fast(self):
        import time
        boilerplate = "<nav><a href='/'>home</a></nav>" * 60
        normal = boilerplate + "".join("<li>row %d</li>" % i for i in range(50))
        empty = boilerplate + "<p>No matches.</p>"
        responses = {
            "p%d" % i: {"baseline": {"body": normal},
                        "true": {"payload": "' OR '1'='1", "body": normal},
                        "false": {"payload": "' OR '1'='2", "body": empty}}
            for i in range(100)
        }
        load(URL, responses)
        started = time.time()
        out = check(URL, list(responses))
        elapsed = time.time() - started
        self.assertEqual(len(out["findings"]), 100)
        self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main()
