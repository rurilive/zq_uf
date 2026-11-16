import unittest

from corpusmin import add, minimize, report

CORPUS = [
    {"id": "c1", "project": "pdf", "size": 120},
    {"id": "c2", "project": "pdf", "size": 80},
    {"id": "c3", "project": "img", "size": 40},
]


class CorpusTest(unittest.TestCase):
    def test_minimize_keeps_every_id(self):
        result = minimize(CORPUS)
        self.assertEqual(result["kept"], ["c1", "c2", "c3"])

    def test_minimize_drops_nothing(self):
        self.assertEqual(minimize(CORPUS)["dropped"], [])

    def test_minimize_has_no_coverage_numbers(self):
        result = minimize(CORPUS)
        self.assertEqual(result["coverage_before"], 0)
        self.assertEqual(result["coverage_after"], 0)

    def test_report_counts_by_project(self):
        minimize(CORPUS)
        body = report()
        self.assertEqual(body["projects"], {"pdf": 2, "img": 1})
        self.assertEqual(body["total"], 3)

    def test_add_appends_without_cover(self):
        minimize(CORPUS)
        result = add([{"id": "c4", "project": "pdf", "size": 10}])
        self.assertEqual(result, {"added": 1, "total": 4})
