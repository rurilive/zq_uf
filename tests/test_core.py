import unittest

from dupfinder import DupFinder

BODY = ("近日记者从市交通委获悉，地铁三号线二期工程已完成主体结构封顶，"
        "预计明年六月开始联调联试，年底具备通车条件。线路全长十八公里，"
        "共设车站十二座，建成后将显著缓解东部城区的通勤压力。")


class DupfinderTest(unittest.TestCase):
    def test_exact_match(self):
        finder = DupFinder()
        finder.index([{"id": "a", "text": "hello"}])
        self.assertEqual(finder.query({"text": "hello"}), ["a"])

    def test_no_match(self):
        finder = DupFinder()
        finder.index([{"id": "a", "text": "hello"}])
        self.assertEqual(finder.query({"text": "hello!"}), [])

    def test_index_count(self):
        finder = DupFinder()
        self.assertEqual(finder.index([{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]), 2)

    def test_title_changed_repost(self):
        finder = DupFinder()
        finder.index([{"id": "a", "title": "地铁三号线年底通车", "text": BODY}])
        hits = finder.query({"title": "东部通勤利好！新线来了", "text": BODY})
        self.assertEqual(hits, ["a"])

    def test_long_short_containment(self):
        short = " ".join("s%d" % i for i in range(10))
        long = short + " " + " ".join("x%d" % i for i in range(100))
        finder = DupFinder()
        finder.index([{"id": "long", "text": long}])
        self.assertEqual(finder.query({"text": short}), ["long"])

    def test_incremental_index(self):
        finder = DupFinder()
        self.assertEqual(finder.index([{"id": "a", "text": BODY}]), 1)
        self.assertEqual(finder.query({"id": "a", "text": BODY}), [])
        self.assertEqual(finder.index([{"id": "b", "text": BODY}]), 2)
        self.assertEqual(finder.query({"id": "c", "text": BODY}), ["a", "b"])

    def test_threshold_boundary(self):
        finder = DupFinder(threshold=0.5)
        finder.index([
            {"id": "eq", "text": "w1 w2 w3 w4 w5 w6 w7 w8"},
            {"id": "below", "text": "w1 w2 w3 w4 w5 w6 w7 w8 w9"},
        ])
        self.assertEqual(finder.query({"text": "w1 w2 w3 w4"}), ["eq"])

    def test_persistence(self):
        import os
        import tempfile
        finder = DupFinder()
        finder.index([{"id": "a", "text": BODY}])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.pkl")
            finder.save(path)
            loaded = DupFinder.load(path)
        self.assertEqual(loaded.query({"text": BODY}), ["a"])
        loaded.index([{"id": "b", "text": BODY}])
        self.assertEqual(loaded.query({"id": "c", "text": BODY}), ["a", "b"])

    def test_explain_fields(self):
        finder = DupFinder()
        finder.index([{"id": "a", "text": BODY}])
        hits = finder.explain({"text": BODY})
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit["id"], "a")
        self.assertEqual(hit["similarity"], 1.0)
        self.assertTrue(hit["common"])
        self.assertIn("阈值", hit["reason"])
