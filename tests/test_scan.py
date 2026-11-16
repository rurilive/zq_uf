import unittest

from webshell import report, scan

FILES = {
    "upload/avatar.php": "<?php\neval($_POST['c']);\n",
    "upload/util.php": "<?php\nreturn 1;\n",
}


class ScanTest(unittest.TestCase):
    def test_keyword_hit_keeps_file_name(self):
        result = scan(FILES)
        self.assertEqual(result["hits"], 1)
        self.assertEqual(result["files"], ["upload/avatar.php"])

    def test_clean_file_is_not_reported(self):
        self.assertEqual(scan({"upload/util.php": "return 1;\n"})["hits"], 0)

    def test_obfuscated_call_is_missed(self):
        text = "<?php\n$f = 'ev'.'al';\n$f($_POST['c']);\n"
        self.assertEqual(scan({"upload/loader.php": text})["hits"], 0)

    def test_repeat_hits_are_not_merged(self):
        text = "eval($_GET['a']);\neval($_GET['b']);\n"
        self.assertEqual(scan({"upload/two.php": text})["hits"], 2)

    def test_report_counts_hits_by_file(self):
        scan(FILES)
        body = report()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["counts"], {"upload/avatar.php": 1})
