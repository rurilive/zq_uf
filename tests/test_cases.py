import json
import os
import tempfile
import unittest

from wafregress.cases import group_by_biz, load_case_files
from wafregress.errors import DuplicateCaseId, WafValidationError

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "cases")


class LoadCasesTest(unittest.TestCase):
    def test_load_directory_grouped_by_biz(self):
        cases = load_case_files([DATA_DIR])
        ids = {case.id for case in cases}
        self.assertEqual(ids, {"s1", "s2", "p1", "p2", "p3", "l1", "l2"})

        grouped = group_by_biz(cases)
        self.assertEqual(sorted(grouped), ["login", "pay", "search"])
        # 文件名作为缺省业务线
        self.assertTrue(all(case.biz == "pay" for case in grouped["pay"]))

    def test_load_single_file(self):
        cases = load_case_files([os.path.join(DATA_DIR, "search.json")])
        self.assertEqual([case.id for case in cases], ["s1", "s2"])
        self.assertTrue(all(case.biz == "search" for case in cases))

    def test_explicit_biz_overrides_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "search.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([{"id": "x1", "biz": "custom", "request": "/x",
                            "expect": "pass"}], fh)
            cases = load_case_files([path])
            self.assertEqual(cases[0].biz, "custom")

    def test_duplicate_id_across_files_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = [{"id": "dup", "request": "/x", "expect": "pass"}]
            for name in ("a.json", "b.json"):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
            with self.assertRaises(DuplicateCaseId):
                load_case_files([tmp])

    def test_missing_path_and_bad_json(self):
        with self.assertRaises(WafValidationError):
            load_case_files(["/nonexistent/path/xyz"])
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            with self.assertRaises(WafValidationError):
                load_case_files([bad])


if __name__ == "__main__":
    unittest.main()
