import unittest

from sbomlayers import report, reset, sbom


class LayersTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_duplicate_component_kept(self):
        got = sbom({
            "image": "app:1",
            "layers": [
                {"id": "l1", "add": [{"name": "openssl", "version": "1.1"}]},
                {"id": "l2", "add": [{"name": "openssl", "version": "1.1"}]},
            ],
        })
        self.assertEqual(len(got["components"]), 2)

    def test_removed_component_still_listed(self):
        got = sbom({
            "image": "app:2",
            "layers": [
                {"id": "l1", "add": [{"name": "curl", "version": "7.0"}]},
                {"id": "l2", "add": [], "remove": ["curl"]},
            ],
        })
        self.assertEqual(got["components"][0]["name"], "curl")

    def test_no_digest(self):
        got = sbom({"image": "app:3", "layers": []})
        self.assertEqual(got["digest"], "")

    def test_image_required(self):
        with self.assertRaises(ValueError):
            sbom({"layers": []})

    def test_report_counts_duplicates(self):
        sbom({"image": "app:5", "layers": [{"id": "l1", "add": [{"name": "a"}, {"name": "a"}]}]})
        self.assertEqual(report()["components"], 2)
