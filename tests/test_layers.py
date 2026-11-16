import unittest

from sbomlayers import report, reset, sbom


class LayersTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_image_required(self):
        with self.assertRaises(ValueError):
            sbom({"layers": []})

    def test_duplicate_component_merged_to_one(self):
        got = sbom({
            "image": "app:1",
            "layers": [
                {"id": "l1", "add": [{"name": "openssl", "version": "1.1"}]},
                {"id": "l2", "add": [{"name": "openssl", "version": "1.1"}]},
            ],
        })
        self.assertEqual(len(got["components"]), 1)
        self.assertEqual(got["components"][0]["layers"], ["l1", "l2"])

    def test_higher_layer_without_version_keeps_lower_version(self):
        got = sbom({
            "image": "app:15",
            "layers": [
                {"id": "l1", "add": [{"name": "openssl", "version": "1.1"}]},
                {"id": "l2", "add": [{"name": "openssl"}]},
            ],
        })
        self.assertEqual(got["components"][0]["version"], "1.1")
        self.assertEqual(got["components"][0]["layer"], "l2")

    def test_removed_component_kept_with_marker(self):
        got = sbom({
            "image": "app:2",
            "layers": [
                {"id": "l1", "add": [{"name": "curl", "version": "7.0"}]},
                {"id": "l2", "add": [], "remove": ["curl"]},
            ],
        })
        self.assertEqual(len(got["components"]), 1)
        curl = got["components"][0]
        self.assertEqual(curl["name"], "curl")
        self.assertEqual(curl["status"], "removed")
        self.assertTrue(curl["removed"])
        self.assertEqual(curl["removed_in"], "l2")

    def test_removed_then_readded_is_present(self):
        got = sbom({
            "image": "app:4",
            "layers": [
                {"id": "l1", "add": [{"name": "curl", "version": "7.0"}]},
                {"id": "l2", "remove": ["curl"]},
                {"id": "l3", "add": [{"name": "curl", "version": "8.0"}]},
            ],
        })
        self.assertEqual(len(got["components"]), 1)
        curl = got["components"][0]
        self.assertEqual(curl["status"], "present")
        self.assertEqual(curl["version"], "8.0")
        self.assertEqual(curl["layer"], "l3")

    def test_removal_marks_entry_added_in_lower_layer(self):
        got = sbom({
            "image": "app:9",
            "layers": [
                {"id": "base", "add": [{"name": "openssl", "version": "1.1"}]},
                {"id": "top", "remove": [{"name": "openssl"}]},
            ],
        })
        self.assertEqual(got["counts"]["components"], 0)
        self.assertEqual(got["counts"]["removed"], 1)

    def test_never_added_removal_still_recorded(self):
        got = sbom({"image": "app:10", "layers": [{"id": "l1", "remove": ["libfoo"]}]})
        self.assertEqual(got["components"][0]["name"], "libfoo")
        self.assertEqual(got["components"][0]["status"], "removed")
        self.assertEqual(got["components"][0]["removed_in"], "l1")

    def test_higher_layer_version_wins_and_diff_recorded(self):
        got = sbom({
            "image": "app:11",
            "layers": [
                {"id": "l1", "add": [{"name": "openssl", "version": "1.1"}]},
                {"id": "l2", "add": [{"name": "openssl", "version": "3.0"}]},
            ],
        })
        self.assertEqual(got["components"][0]["version"], "3.0")
        self.assertEqual(got["components"][0]["layer"], "l2")
        self.assertEqual(got["layers"][1]["replaced"],
                         [{"name": "openssl", "from": "1.1", "to": "3.0"}])

    def test_license_and_version_present(self):
        got = sbom({
            "image": "app:12",
            "layers": [{"id": "l1", "add": [
                {"name": "openssl", "version": "1.1", "license": "OpenSSL"},
                {"name": "zlib", "version": "1.3", "licenses": ["Zlib"]},
            ]}],
        })
        by_name = {c["name"]: c for c in got["components"]}
        self.assertEqual(by_name["openssl"]["license"], "OpenSSL")
        self.assertEqual(by_name["zlib"]["license"], "Zlib")
        self.assertEqual(by_name["openssl"]["version"], "1.1")

    def test_digest_and_scanned_at_present(self):
        got = sbom({"image": "app:3", "layers": []})
        self.assertTrue(got["digest"].startswith("sha256:"))
        self.assertTrue(got["scanned_at"])

    def test_digest_is_stable_and_time_is_explicit(self):
        payload = {
            "image": "app:6",
            "digest": "sha256:abc",
            "scanned_at": "2026-01-01T00:00:00+00:00",
            "layers": [{"id": "l1", "add": [{"name": "a", "version": "1"}]}],
        }
        first = sbom(payload)
        second = sbom(payload)
        self.assertEqual(first["digest"], "sha256:abc")
        self.assertEqual(first["scanned_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(first["digest"], second["digest"])

    def test_computed_digest_changes_with_layer_content(self):
        a = sbom({"image": "app:7", "layers": [
            {"id": "l1", "add": [{"name": "a", "version": "1"}]}]})
        b = sbom({"image": "app:7", "layers": [
            {"id": "l1", "add": [{"name": "a", "version": "2"}]}]})
        self.assertNotEqual(a["digest"], b["digest"])

    def test_layer_records_keep_add_and_remove(self):
        got = sbom({
            "image": "app:8",
            "layers": [
                {"id": "l1", "add": [
                    {"name": "a", "version": "1"},
                    {"name": "b", "version": "1"}]},
                {"id": "l2", "add": [{"name": "c", "version": "1"}],
                 "remove": ["b"]},
            ],
        })
        first, second = got["layers"]
        self.assertEqual([c["name"] for c in first["added"]], ["a", "b"])
        self.assertEqual([c["name"] for c in second["added"]], ["c"])
        self.assertEqual([c["name"] for c in second["removed"]], ["b"])

    def test_bad_layer_reports_error(self):
        with self.assertRaises(ValueError):
            sbom({"image": "app:13", "layers": ["not-a-layer"]})
        with self.assertRaises(ValueError):
            sbom({"image": "app:14", "layers": [{"id": "l1", "add": [{}]}]})


class DiffTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_second_scan_reports_component_changes(self):
        sbom({"image": "app:1", "layers": [
            {"id": "l1", "add": [
                {"name": "openssl", "version": "1.1"},
                {"name": "curl", "version": "7.0"},
                {"name": "zlib", "version": "1.3"}]}]})
        got = sbom({"image": "app:1", "layers": [
            {"id": "l1", "add": [
                {"name": "openssl", "version": "3.0"},
                {"name": "zlib", "version": "1.3"},
                {"name": "libc", "version": "2.0"}]}]})
        changes = {(d["name"], d["change"]) for d in got["diff"]}
        self.assertEqual(changes, {
            ("openssl", "version_changed"),
            ("curl", "removed"),
            ("libc", "added"),
        })
        curl = [d for d in got["diff"] if d["name"] == "curl"][0]
        self.assertEqual(curl["from"], "7.0")
        self.assertIsNone(curl["to"])

    def test_first_scan_has_empty_diff(self):
        got = sbom({"image": "app:1", "layers": [
            {"id": "l1", "add": [{"name": "a", "version": "1"}]}]})
        self.assertEqual(got["diff"], [])

    def test_same_digest_with_changed_components_warns(self):
        sbom({"image": "app:1", "digest": "sha256:x", "layers": [
            {"id": "l1", "add": [{"name": "a", "version": "1"}]}]})
        got = sbom({"image": "app:1", "digest": "sha256:x", "layers": [
            {"id": "l1", "add": [{"name": "a", "version": "2"}]}]})
        types = {w["type"] for w in got["warnings"]}
        self.assertIn("digest_components_mismatch", types)
        self.assertTrue(any(d["name"] == "a" for d in got["diff"]))

    def test_component_digest_mismatch_warns(self):
        got = sbom({"image": "app:1", "digest": "sha256:image", "layers": [
            {"id": "l1", "add": [
                {"name": "a", "version": "1", "digest": "sha256:other"}]}]})
        self.assertTrue(any(
            w["type"] == "component_digest_mismatch" and w["name"] == "a"
            for w in got["warnings"]))


class CompareTest(unittest.TestCase):
    def setUp(self):
        reset()
        sbom({"image": "app:1", "layers": [
            {"id": "l1", "add": [
                {"name": "openssl", "version": "1.1", "license": "OpenSSL"},
                {"name": "curl", "version": "7.0", "license": "curl"}]}]})
        sbom({"image": "app:2", "layers": [
            {"id": "l1", "add": [
                {"name": "openssl", "version": "3.0", "license": "Apache-2.0"},
                {"name": "libc", "version": "2.0", "license": "LGPL"}]}]})

    def test_compare_split_counters(self):
        got = report("app:1", "app:2")
        self.assertEqual([c["name"] for c in got["added"]], ["libc"])
        self.assertEqual([c["name"] for c in got["removed"]], ["curl"])
        self.assertEqual([c["name"] for c in got["version_changed"]], ["openssl"])
        self.assertEqual(got["counts"]["added"], 1)
        self.assertEqual(got["counts"]["removed"], 1)
        self.assertEqual(got["counts"]["version_changed"], 1)
        self.assertEqual(got["counts"]["unchanged"], 0)

    def test_compare_version_change_keeps_both_versions(self):
        got = report("app:1", "app:2")
        change = got["version_changed"][0]
        self.assertEqual((change["from"], change["to"]), ("1.1", "3.0"))
        self.assertEqual(change["from_license"], "OpenSSL")
        self.assertEqual(change["to_license"], "Apache-2.0")

    def test_compare_ignores_layer_only_differences(self):
        reset()
        sbom({"image": "a", "layers": [
            {"id": "l1", "add": [{"name": "x", "version": "1"}]},
            {"id": "l2", "add": [{"name": "x", "version": "1"}]}]})
        sbom({"image": "b", "layers": [
            {"id": "l1", "add": [{"name": "x", "version": "1"}]}]})
        got = report("a", "b")
        self.assertEqual(got["counts"]["unchanged"], 1)

    def test_unknown_image_raises(self):
        with self.assertRaises(KeyError):
            report("missing:1")

    def test_report_single_image_has_layers(self):
        got = report("app:1")
        self.assertEqual(got["image"], "app:1")
        self.assertTrue(got["digest"])
        self.assertTrue(got["layers"])


class ReportTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_report_dedupes_within_image(self):
        sbom({"image": "app:5", "layers": [
            {"id": "l1", "add": [{"name": "a", "version": "1"}]},
            {"id": "l2", "add": [{"name": "a", "version": "1"}]}]})
        self.assertEqual(report()["components"], 1)
        self.assertEqual(report()["images"], 1)

    def test_report_aggregates_and_tracks_removed(self):
        sbom({"image": "app:1", "layers": [
            {"id": "l1", "add": [{"name": "a"}, {"name": "b"}]},
            {"id": "l2", "remove": ["b"]}]})
        sbom({"image": "app:2", "layers": [
            {"id": "l1", "add": [{"name": "c"}]}]})
        got = report()
        self.assertEqual(got["images"], 2)
        self.assertEqual(got["components"], 2)  # a + c
        self.assertEqual(got["removed"], 1)     # b 保留为删除记录
        self.assertTrue(got["digest"])
        self.assertIn("app:1", got["details"])

    def test_report_diff_includes_image(self):
        sbom({"image": "app:1", "layers": [{"id": "l1", "add": [{"name": "a"}]}]})
        sbom({"image": "app:1", "layers": [{"id": "l1", "add": [{"name": "b"}]}]})
        diffs = report()["diff"]
        self.assertTrue(all("image" in item for item in diffs))
        self.assertEqual({d["change"] for d in diffs}, {"added", "removed"})

    def test_empty_report(self):
        got = report()
        self.assertEqual(got["images"], 0)
        self.assertEqual(got["components"], 0)
        self.assertEqual(got["digest"], "")


if __name__ == "__main__":
    unittest.main()
