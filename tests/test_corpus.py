import unittest

from corpusmin import add, minimize, report, reset, restore

CORPUS = [
    {"id": "c1", "project": "pdf", "size": 40, "covers": ["parser.c:1-20"]},
    {"id": "c2", "project": "pdf", "size": 120, "covers": ["parser.c:1-20"]},
    {"id": "c3", "project": "img", "size": 40, "covers": ["png.c:1-9"]},
]


class MinimizeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_drops_redundant_item(self):
        result = minimize(CORPUS)
        self.assertEqual(result["kept"], ["c1", "c3"])
        self.assertEqual(result["dropped"], ["c2"])
        self.assertEqual(result["kept_count"], 2)
        self.assertEqual(result["dropped_count"], 1)

    def test_coverage_is_preserved(self):
        result = minimize(CORPUS)
        self.assertEqual(result["coverage_before"], result["coverage_after"])
        self.assertEqual(result["coverage_delta"], 0)
        self.assertTrue(result["coverage_preserved"])
        self.assertEqual(result["coverage_change"]["lost_regions"], [])

    def test_contributions_add_up_to_coverage(self):
        corpus = [
            {"id": "a", "project": "p", "covers": ["x:1", "x:2", "x:3"]},
            {"id": "b", "project": "p", "covers": ["x:2", "x:4"]},
            {"id": "c", "project": "p", "covers": ["x:1"]},
        ]
        result = minimize(corpus)
        contributed = set()
        for regions in result["contributions"].values():
            contributed.update(regions)
        self.assertEqual(contributed, set(result["coverage_by_region"]))
        self.assertEqual(len(contributed), result["coverage_after"])
        self.assertIn("x:1", result["contributions"]["a"])
        # b 只带来 x:4，所以它被保留并记录了增量贡献。
        self.assertEqual(result["contributions"]["b"], ["x:4"])
        self.assertEqual(result["dropped"], ["c"])

    def test_dropped_items_go_to_project_archive(self):
        result = minimize(CORPUS)
        record = result["archive"]["c2"]
        self.assertEqual(record["project"], "pdf")
        self.assertEqual(record["reason"], "fully_covered")
        self.assertEqual(record["covers"], ["parser.c:1-20"])
        self.assertEqual(record["covered_by"], ["c1"])
        self.assertEqual(record["entry"]["id"], "c2")
        self.assertEqual(report("pdf")["archive"]["c2"]["reason"], "fully_covered")

    def test_empty_coverage_is_dropped_but_recoverable(self):
        corpus = [
            {"id": "a", "project": "p", "covers": ["x:1"]},
            {"id": "noop", "project": "p", "covers": []},
        ]
        result = minimize(corpus)
        self.assertEqual(result["dropped"], ["noop"])
        self.assertEqual(result["archive"]["noop"]["reason"], "no_coverage")
        self.assertTrue(restore("noop")["restored"])

    def test_unknown_coverage_is_kept(self):
        corpus = [
            {"id": "a", "project": "p", "covers": ["x:1"]},
            {"id": "unknown", "project": "p"},
        ]
        result = minimize(corpus)
        self.assertEqual(result["kept"], ["a", "unknown"])
        self.assertEqual(result["unknown_coverage"], ["unknown"])
        self.assertEqual(result["dropped"], [])

    def test_second_run_is_stable(self):
        first = minimize(CORPUS)
        pdf = [item for item in CORPUS if item["id"] in first["kept"] and item["project"] == "pdf"]
        second = minimize(pdf)
        self.assertEqual(second["dropped"], [])
        self.assertEqual(second["kept"], ["c1"])
        self.assertEqual(second["coverage_after"], 1)

    def test_selection_is_deterministic(self):
        reset()
        one = minimize(CORPUS)["kept"]
        reset()
        two = minimize(CORPUS)["kept"]
        self.assertEqual(one, two)

    def test_empty_corpus(self):
        result = minimize([])
        self.assertEqual(result["kept"], [])
        self.assertEqual(result["dropped"], [])
        self.assertEqual(result["coverage_before"], 0)
        self.assertEqual(result["coverage_after"], 0)
        self.assertTrue(result["coverage_preserved"])

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            minimize("not a list")
        with self.assertRaises(ValueError):
            minimize([{"project": "p"}])
        with self.assertRaises(ValueError):
            minimize([{"id": "a"}, {"id": "a"}])


class RegionTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_solo_regions_are_flagged(self):
        corpus = [
            {"id": "a", "project": "p", "covers": ["only_a:1", "shared:1"]},
            {"id": "b", "project": "p", "covers": ["shared:1"]},
        ]
        result = minimize(corpus)
        # 收敛前只有 a 覆盖 only_a:1；收敛后 b 被丢弃，shared:1 也只剩 a。
        self.assertEqual(result["solo_regions_before"], ["only_a:1"])
        self.assertEqual(result["solo_regions"], ["only_a:1", "shared:1"])
        self.assertEqual(result["sole_source"], {"only_a:1": "a", "shared:1": "a"})
        self.assertEqual(result["critical"], ["a"])
        self.assertEqual(result["critical_coverage"], {"a": ["only_a:1"]})
        detail = result["coverage_by_region"]["shared:1"]
        self.assertEqual(detail["solo_before"], False)
        self.assertEqual(detail["solo_after"], True)
        self.assertEqual(detail["kept_by"], ["a"])

    def test_region_comparison_before_and_after(self):
        corpus = [
            {"id": "a", "project": "p", "covers": ["x:1", "x:2"]},
            {"id": "b", "project": "p", "covers": ["x:2"]},
        ]
        detail = minimize(corpus)["coverage_by_region"]
        self.assertEqual(detail["x:1"]["before"], 1)
        self.assertEqual(detail["x:1"]["after"], 1)
        self.assertEqual(detail["x:2"]["before"], 2)
        self.assertEqual(detail["x:2"]["after"], 1)
        self.assertEqual(detail["x:2"]["sources_before"], ["a", "b"])
        self.assertEqual(detail["x:2"]["kept_by"], ["a"])

    def test_region_detail_after_restore(self):
        corpus = [
            {"id": "a", "project": "p", "covers": ["x:1"]},
            {"id": "b", "project": "p", "covers": ["x:2"]},
        ]
        minimize(corpus)
        restore("b")
        detail = report("p")["coverage_by_region"]
        self.assertEqual(detail["x:2"]["count"], 1)
        self.assertEqual(detail["x:2"]["solo"], True)

    def test_file_line_coverage_format(self):
        corpus = [
            {"id": "a", "project": "p", "coverage": {"f.c": [1, 2], "g.c": [3]}},
            {"id": "b", "project": "p", "coverage": {"f.c": [1]}},
        ]
        result = minimize(corpus)
        self.assertEqual(
            result["coverage_by_region"],
            {
                "f.c:1": {
                    "covered_before": True,
                    "covered_after": True,
                    "before": 2,
                    "after": 1,
                    "solo_before": False,
                    "solo_after": True,
                    "solo": True,
                    "sources_before": ["a", "b"],
                    "kept_by": ["a"],
                },
                "f.c:2": {
                    "covered_before": True,
                    "covered_after": True,
                    "before": 1,
                    "after": 1,
                    "solo_before": True,
                    "solo_after": True,
                    "solo": True,
                    "sources_before": ["a"],
                    "kept_by": ["a"],
                },
                "g.c:3": {
                    "covered_before": True,
                    "covered_after": True,
                    "before": 1,
                    "after": 1,
                    "solo_before": True,
                    "solo_after": True,
                    "solo": True,
                    "sources_before": ["a"],
                    "kept_by": ["a"],
                },
            },
        )
        self.assertEqual(result["dropped"], ["b"])


class ProjectTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_projects_are_separate(self):
        minimize(CORPUS)
        body = report()
        self.assertEqual(body["projects"]["pdf"]["total"], 1)
        self.assertEqual(body["projects"]["img"]["total"], 1)
        self.assertEqual(body["projects"]["pdf"]["dropped_count"], 1)
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["dropped"], 1)

    def test_project_result_has_details(self):
        minimize(CORPUS)
        pdf = report("pdf")
        self.assertEqual(pdf["kept_ids"], ["c1"])
        self.assertEqual(pdf["dropped_ids"], ["c2"])
        self.assertEqual(pdf["size"], 40)
        self.assertEqual(pdf["regions"], 1)
        self.assertEqual(pdf["solo_regions"], ["parser.c:1-20"])
        self.assertEqual(pdf["sole_source"], {"parser.c:1-20": "c1"})

    def test_unknown_project_report(self):
        body = report("nope")
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["rounds"], [])

    def test_multi_project_minimize(self):
        result = minimize(CORPUS)
        self.assertIsNone(result["project"])
        self.assertEqual(set(result["projects"]), {"pdf", "img"})
        self.assertEqual(result["coverage_after"], 2)
        self.assertEqual(result["size_before"], 200)
        self.assertEqual(result["size_after"], 80)


class RestoreTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_restore_brings_back_entry_and_coverage(self):
        minimize(CORPUS)
        result = restore("c2")
        self.assertEqual(result["restored"], ["c2"])
        self.assertEqual(result["coverage_delta"], 0)
        pdf = report("pdf")
        self.assertIn("c2", pdf["kept_ids"])
        self.assertEqual(pdf["coverage_by_region"]["parser.c:1-20"]["count"], 2)
        self.assertNotIn("c2", pdf["archive"])

    def test_restore_returns_original_payload(self):
        corpus = [{"id": "a", "project": "p", "covers": ["x:1"], "note": "keep me"}]
        corpus.append({"id": "b", "project": "p", "covers": ["x:1"], "note": "bye"})
        minimize(corpus)
        restore("b")
        body = report("p")
        self.assertIn("b", body["kept_ids"])

    def test_restore_unknown_id(self):
        minimize(CORPUS)
        result = restore("missing")
        self.assertEqual(result["restored"], [])
        self.assertEqual(result["missing"], ["missing"])

    def test_restore_already_present(self):
        minimize(CORPUS)
        result = restore("c1")
        self.assertEqual(result["restored"], [])
        self.assertEqual(result["already_present"], ["c1"])

    def test_restore_scoped_to_project(self):
        minimize(CORPUS)
        result = restore("c2", project="img")
        self.assertEqual(result["restored"], [])
        self.assertEqual(result["missing"], ["c2"])

    def test_restore_later_makes_coverage_persistent(self):
        corpus = [
            {"id": "a", "project": "p", "covers": ["x:1"]},
            {"id": "b", "project": "p", "covers": ["x:1", "x:9"]},
        ]
        first = minimize(corpus)
        # 覆盖全部保留后，收敛把重复的 a 丢掉；再取回它。
        self.assertEqual(first["coverage_after"], 2)
        self.assertEqual(first["dropped"], ["a"])
        restore("a")
        second = minimize([{"id": "b", "project": "p", "covers": ["x:1", "x:9"]}])
        self.assertIn("a", report("p")["archive"])
        self.assertEqual(second["coverage_after"], 2)


class AddTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_add_records_new_coverage(self):
        minimize([{"id": "a", "project": "p", "covers": ["x:1"]}])
        result = add([{"id": "b", "project": "p", "covers": ["x:1", "x:2"]}])
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["new_ids"], ["b"])
        self.assertEqual(result["new_regions"], 1)
        self.assertEqual(result["total"], 2)
        pdf = report("p")
        self.assertEqual(pdf["contributions"]["b"], ["x:2"])

    def test_add_flags_duplicates_without_dropping(self):
        minimize([{"id": "a", "project": "p", "covers": ["x:1"]}])
        result = add([{"id": "b", "project": "p", "covers": ["x:1"]}])
        self.assertEqual(result["duplicate_ids"], ["b"])
        self.assertEqual(result["new_regions"], 0)
        self.assertIn("b", report("p")["kept_ids"])
        # 下一次 minimize 才会清掉它。
        self.assertEqual(minimize([
            {"id": "a", "project": "p", "covers": ["x:1"]},
            {"id": "b", "project": "p", "covers": ["x:1"]},
        ])["dropped"], ["b"])

    def test_add_to_other_project_does_not_touch_first(self):
        minimize([{"id": "a", "project": "p", "covers": ["x:1"]}])
        add([{"id": "z", "project": "q", "covers": ["y:1"]}])
        self.assertEqual(report("p")["coverage"], 1)
        self.assertEqual(report("q")["coverage"], 1)


class RoundHistoryTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_rounds_are_recorded(self):
        minimize(CORPUS)
        add([{"id": "c4", "project": "pdf", "size": 10, "covers": ["parser.c:99"]}])
        restore("c2")
        pdf = report("pdf")
        self.assertEqual([r["action"] for r in pdf["rounds"]], ["minimize", "add", "restore"])
        self.assertEqual(pdf["latest_round"], 3)
        body = report()
        # img 只经历过 minimize，所以总轮次是 pdf 的 3 条加 img 的 1 条。
        self.assertEqual(len(body["rounds"]), 4)
        self.assertEqual(
            [(r["round"], r["project"], r["action"]) for r in body["rounds"]],
            [(1, "img", "minimize"), (1, "pdf", "minimize"), (2, "pdf", "add"), (3, "pdf", "restore")],
        )

    def test_round_snapshot_keeps_coverage(self):
        minimize(CORPUS)
        round_one = report("pdf")["rounds"][0]
        self.assertEqual(round_one["coverage_before"], 1)
        self.assertEqual(round_one["coverage_after"], 1)
        self.assertEqual(round_one["source_refs_before"], 2)
        self.assertEqual(round_one["source_refs_after"], 1)
        self.assertEqual(round_one["dropped"], ["c2"])
        self.assertEqual(round_one["critical"], [])
        self.assertEqual(round_one["region_counts"], {"parser.c:1-20": 1})
        self.assertEqual(round_one["regions_lost"], [])


if __name__ == "__main__":
    unittest.main()
