import unittest

from ssrfverify import check, report, reset


class ProbeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_marker_means_hit(self):
        got = check("http://10.0.0.5/item?id=1", "id", "root:x:0:0")
        self.assertTrue(got["hit"])

    def test_plain_text_is_miss(self):
        got = check("http://10.0.0.5/item?id=2", "id", "not found")
        self.assertFalse(got["hit"])

    def test_no_callback_record(self):
        got = check("http://10.0.0.5/item?id=3", "id", "internal")
        self.assertIsNone(got["callback"])

    def test_report_keeps_duplicates(self):
        url = "http://10.0.0.5/item?id=4"
        check(url, "id", "admin")
        check(url, "id", "admin")
        self.assertEqual(report()["hits"].count(url), 2)

    def test_param_required(self):
        with self.assertRaises(ValueError):
            check("http://10.0.0.5/item", "", "ok")
