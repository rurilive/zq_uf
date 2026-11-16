import unittest

from corpusmin import add, minimize, report, restore
from corpusmin.corpus import _reset

CORPUS = [
    {"id": "c1", "project": "pdf", "size": 120,
     "coverage": ["parser:header", "parser:body", "xref:read"]},
    {"id": "c2", "project": "pdf", "size": 80,
     "coverage": ["parser:body"]},
    {"id": "c3", "project": "img", "size": 40,
     "coverage": ["decode:png"]},
]


class CorpusTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_minimize_drops_redundant_item(self):
        result = minimize(CORPUS)
        self.assertEqual(result["kept"], ["c1", "c3"])
        self.assertEqual(result["dropped"], ["c2"])
        self.assertEqual(result["kept_count"], 2)
        self.assertEqual(result["dropped_count"], 1)

    def test_minimize_preserves_coverage(self):
        result = minimize(CORPUS)
        self.assertEqual(result["coverage_before"], 4)
        self.assertEqual(result["coverage_after"], 4)
        self.assertEqual(result["coverage_delta"], 0)

    def test_dropped_items_go_to_archive(self):
        result = minimize(CORPUS)
        self.assertEqual(result["archive"], {"c2": "pdf/r1/c2"})

    def test_restore_brings_back_coverage(self):
        minimize(CORPUS)
        result = restore("pdf", ["c2"])
        self.assertEqual(result["restored"], ["c2"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["coverage_restored"], 1)
        self.assertEqual(report()["projects"]["pdf"]["archived"], 0)

    def test_restore_unknown_id_is_reported_missing(self):
        minimize(CORPUS)
        result = restore("pdf", ["nope"])
        self.assertEqual(result["restored"], [])
        self.assertEqual(result["missing"], ["nope"])

    def test_report_marks_solo_regions(self):
        minimize(CORPUS)
        body = report("pdf")
        pdf = body["projects"]["pdf"]
        self.assertEqual(pdf["kept"], 1)
        self.assertEqual(pdf["archived"], 1)
        # c1 独撑全部三个区域
        self.assertEqual(
            pdf["solo_regions"],
            ["parser:body", "parser:header", "xref:read"],
        )
        self.assertEqual(pdf["lost_regions"], [])

    def test_report_keeps_round_history(self):
        minimize(CORPUS)
        minimize(CORPUS)
        rounds = report("pdf")["projects"]["pdf"]["rounds"]
        self.assertEqual([r["round"] for r in rounds], [1, 2])
        self.assertEqual(rounds[0]["dropped"], ["c2"])

    def test_projects_are_minimized_independently(self):
        corpus = [
            {"id": "a1", "project": "p1", "coverage": ["r:1"]},
            {"id": "b1", "project": "p2", "coverage": ["r:1"]},
        ]
        result = minimize(corpus)
        # 同名区域分属不同项目，互不顶掉
        self.assertEqual(sorted(result["kept"]), ["a1", "b1"])

    def test_add_reports_new_coverage(self):
        minimize(CORPUS)
        result = add([{"id": "c4", "project": "pdf", "size": 10,
                       "coverage": ["parser:body", "encrypt:aes"]}])
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["new_coverage"], 1)

    def test_items_without_coverage_are_kept(self):
        result = minimize([{"id": "x", "project": "p"}])
        self.assertEqual(result["kept"], ["x"])
        self.assertEqual(result["coverage_before"], 0)
