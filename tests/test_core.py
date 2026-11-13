import os
import tempfile
import unittest

from pagesnap import diff, save


class PagesnapTest(unittest.TestCase):
    def test_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = save({"id": "p1", "text": "hello"}, tmp)
            self.assertEqual(open(path).read(), "hello")

    def test_diff_detects_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = save({"id": "a", "text": "one"}, tmp)
            b = save({"id": "b", "text": "two"}, tmp)
            self.assertTrue(diff(a, b)["changed"])


class SnapshotStoreTest(unittest.TestCase):
    def test_dedup_same_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = {"id": "p1", "text": "same body", "html": "<html><body><p>x</p></body></html>"}
            first = save(page, tmp)
            second = save(dict(page, id="p2"), tmp)
            self.assertEqual(first, second)
            objects = os.listdir(os.path.join(tmp, "objects"))
            self.assertEqual(len([f for f in objects if f.endswith(".txt")]), 1)

    def test_get_by_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            save({"id": "p", "site": "news", "text": "v1", "time": 1000.0}, tmp)
            save({"id": "p", "site": "news", "text": "v2", "time": 2000.0}, tmp)
            from pagesnap import get
            self.assertEqual(get("p", tmp, site="news", at=1500.0)["text"], "v1")
            self.assertEqual(get("p", tmp, site="news")["text"], "v2")
            self.assertIsNone(get("p", tmp, site="other"))

    def test_structure_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = save({"id": "a", "text": "hello", "html": "<html><body><p>x</p></body></html>"}, tmp)
            b = save({"id": "b", "text": "hello world",
                      "html": "<html><body><section><p>x</p></section></body></html>"}, tmp)
            result = diff(a, b)
            self.assertTrue(result["changed"])
            self.assertIn("section", result["added_nodes"])
            self.assertGreater(result["structure_change_percent"], 0)
            self.assertGreater(result["text_change_percent"], 0)

    def test_retention_prunes_old(self):
        import time
        from pagesnap import get
        with tempfile.TemporaryDirectory() as tmp:
            old_path = save({"id": "old", "text": "expired",
                             "time": time.time() - 10 * 86400}, tmp)
            save({"id": "new", "text": "fresh"}, tmp, retention_days=7)
            self.assertFalse(os.path.exists(old_path))
            self.assertIsNone(get("old", tmp))
            self.assertEqual(get("new", tmp)["text"], "fresh")
