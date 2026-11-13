import json
import unittest

from fieldsource import extract, extract_many, merge


def rule(field, getter, **meta):
    return {"field": field, "get": getter, **meta}


class CandidatesTest(unittest.TestCase):
    def test_all_candidates_kept(self):
        rules = [rule("price", lambda p: p.get("a")), rule("price", lambda p: p.get("b"))]
        result = extract({"a": "199", "b": "299"}, rules)
        candidates = result.sources["price"]["candidates"]
        self.assertEqual([c["value"] for c in candidates], ["199", "299"])
        self.assertEqual(result["price"], "299")

    def test_priority_picks_primary(self):
        rules = [
            rule("price", lambda p: p.get("a"), priority=10),
            rule("price", lambda p: p.get("b"), priority=1),
        ]
        result = extract({"a": "199", "b": "299"}, rules)
        self.assertEqual(result["price"], "199")
        self.assertEqual(result.sources["price"]["rule"]["id"], "price")
        self.assertEqual(len(result.sources["price"]["candidates"]), 2)

    def test_snippet_and_position(self):
        page = {"desc": "本品售价 299 元包邮"}
        result = extract(page, [rule("price", lambda p: "299")])
        candidate = result.sources["price"]["candidates"][0]
        self.assertEqual(candidate["position"], {"key": "desc", "start": 5, "end": 8})
        self.assertIn("299", candidate["snippet"])


class CrossPageTest(unittest.TestCase):
    PAGES = [
        {"id": "p1", "price": "299"},
        {"id": "p2", "price": "279"},
    ]

    def test_candidates_carry_source_page(self):
        rules = [rule("price", lambda p: p.get("price"), id="price", priority=1)]
        result = extract_many(self.PAGES, rules)
        pages = [c["page"] for c in result.sources["price"]["candidates"]]
        self.assertEqual(pages, ["p1", "p2"])
        self.assertEqual(result["price"], "279")

    def test_merge_keeps_pages(self):
        rules = [rule("price", lambda p: p.get("price"), id="price")]
        results = [extract(p, rules, page_id=p["id"]) for p in self.PAGES]
        merged = merge(results)
        pages = {c["page"] for c in merged.sources["price"]["candidates"]}
        self.assertEqual(pages, {"p1", "p2"})


class VersionTest(unittest.TestCase):
    def test_version_recorded(self):
        rules = [rule("title", lambda p: p["t"], id="title", version="2.1")]
        result = extract({"t": "键盘"}, rules)
        self.assertEqual(result.sources["title"]["rule"]["version"], "2.1")

    def test_rename_traceable_by_id(self):
        old = rule("title", lambda p: p["t"], id="title", name="标题", version="1")
        new = rule("title", lambda p: p["t"], id="title", name="商品标题", version="2")
        old_src = extract({"t": "键盘"}, [old]).sources["title"]["rule"]
        new_src = extract({"t": "键盘"}, [new]).sources["title"]["rule"]
        self.assertEqual(old_src["id"], new_src["id"])
        self.assertNotEqual(old_src["name"], new_src["name"])
        self.assertNotEqual(old_src["version"], new_src["version"])


class SerializeTest(unittest.TestCase):
    def test_json_roundtrip(self):
        rules = [rule("price", lambda p: p.get("a")), rule("price", lambda p: p.get("b"))]
        result = extract({"a": "199", "b": "299"}, rules)
        data = json.loads(result.to_json())
        self.assertEqual(data["values"], {"price": "299"})
        self.assertEqual(len(data["sources"]["price"]["candidates"]), 2)

    def test_size_control(self):
        page = {"desc": "x" * 100 + "299" + "y" * 100}
        rules = [
            rule("price", lambda p: "299"),
            rule("price", lambda p: "299", id="p2"),
            rule("price", lambda p: "299", id="p3"),
        ]
        result = extract(page, rules)
        data = result.to_dict(max_snippet=10, max_candidates=2)
        candidates = data["sources"]["price"]["candidates"]
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(len(c["snippet"]) <= 10 for c in candidates))
        self.assertTrue(all(c["truncated"] for c in candidates))


if __name__ == "__main__":
    unittest.main()
