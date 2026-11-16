import unittest

from samplededupe import (
    compare,
    failures,
    family_view,
    group_view,
    ingest,
    report,
    reset,
    retry_failed,
    set_threshold,
    threshold,
)

BASE = bytes(range(256)) * 8


def one_byte_variant(offset, value=0x5A):
    data = bytearray(BASE)
    data[offset] ^= value
    return bytes(data)


class StoreTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_new_sample_is_added(self):
        out = ingest({"name": "locker-a.bin", "digest": "aa11", "size": 2048})
        self.assertTrue(out["added"])
        self.assertEqual(out["status"], "added")

    def test_same_digest_is_duplicate(self):
        ingest({"name": "locker-a.bin", "digest": "aa11", "family": "ransom"})
        out = ingest({"name": "locker-b.bin", "digest": "aa11", "family": "ransom"})
        self.assertEqual(out["status"], "duplicate")
        self.assertFalse(out["added"])
        self.assertEqual(out["family"], "ransom")

    def test_one_byte_change_counts_as_new(self):
        ingest({"name": "locker-a.bin", "digest": "aa11"})
        out = ingest({"name": "locker-a1.bin", "digest": "aa12"})
        self.assertTrue(out["added"])

    def test_missing_family_is_unlabeled(self):
        out = ingest({"name": "drop.bin", "digest": "cc33"})
        self.assertEqual(out["family"], "unlabeled")

    def test_report_counts_total_and_duplicates(self):
        ingest({"name": "locker-a.bin", "digest": "aa11", "family": "ransom"})
        ingest({"name": "locker-b.bin", "digest": "aa11", "family": "ransom"})
        ingest({"name": "rat.bin", "digest": "cc33", "family": "rat"})
        out = report()
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["duplicates"], 1)
        self.assertEqual(out["families"], {"ransom": 1, "rat": 1})


class SimilarityGroupTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_near_variants_share_a_group(self):
        ingest({"name": "a.bin", "digest": "d1", "family": "ransom",
                "content": BASE, "size": len(BASE)})
        out = ingest({"name": "a1.bin", "digest": "d2", "family": "ransom",
                      "content": one_byte_variant(100), "size": len(BASE)})
        self.assertTrue(out["added"])
        self.assertTrue(out["similar"])
        self.assertEqual(out["group"], "ransom#1")
        self.assertEqual(out["variant_count"], 2)

    def test_insertion_does_not_break_similarity(self):
        ingest({"name": "a.bin", "digest": "d1", "family": "f", "content": BASE})
        out = ingest({"name": "prefixed.bin", "digest": "d2", "family": "f",
                      "content": b"header" + BASE})
        self.assertTrue(out["similar"])
        self.assertEqual(out["group"], "f#1")

    def test_unrelated_samples_stay_apart(self):
        ingest({"name": "a.bin", "digest": "d1", "family": "ransom",
                "content": BASE})
        other = bytes(reversed(BASE)) + b"tail" * 50
        out = ingest({"name": "b.bin", "digest": "d2", "family": "ransom",
                      "content": other})
        self.assertFalse(out["similar"])
        self.assertNotEqual(out["group"], "ransom#1")

    def test_bridging_sample_merges_two_groups(self):
        import random

        base_rng = random.Random(0)
        base = bytes(base_rng.randrange(256) for _ in range(2000))

        def mutate(edits, seed):
            rng = random.Random(seed)
            data = bytearray(base)
            for offset in rng.sample(range(len(data)), edits):
                data[offset] ^= rng.randrange(1, 256)
            return bytes(data)

        left, right, bridge = mutate(28, 1), mutate(20, 3), mutate(20, 2)
        ingest({"name": "left.bin", "digest": "d1", "family": "f",
                "content": left})
        ingest({"name": "right.bin", "digest": "d2", "family": "f",
                "content": right})
        self.assertEqual(family_view("f")["group_count"], 2)

        out = ingest({"name": "bridge.bin", "digest": "d3", "family": "f",
                      "content": bridge})
        self.assertEqual(family_view("f")["group_count"], 1)
        self.assertEqual(out["group"], "f#1")
        self.assertEqual(out["variant_count"], 3)

    def test_representative_is_medoid(self):
        # 中间样本与另一份内容完全相同（digest 不同），因此与组内每份都很像。
        ingest({"name": "outlier.bin", "digest": "d0", "family": "f",
                "content": one_byte_variant(10)})
        ingest({"name": "mid.bin", "digest": "d1", "family": "f",
                "content": BASE})
        ingest({"name": "mid-copy.bin", "digest": "d2", "family": "f",
                "content": BASE})
        groups = family_view("f")["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["representative"]["name"], "mid.bin")

    def test_representative_tie_prefers_earliest(self):
        ingest({"name": "first.bin", "digest": "d1", "family": "f",
                "content": BASE})
        ingest({"name": "second.bin", "digest": "d2", "family": "f",
                "content": BASE})
        groups = family_view("f")["groups"]
        self.assertEqual(groups[0]["names"], ["first.bin", "second.bin"])
        self.assertEqual(groups[0]["representative"]["name"], "first.bin")

    def test_ingest_returns_difference_positions(self):
        ingest({"name": "a.bin", "digest": "d1", "family": "f",
                "content": BASE, "size": len(BASE)})
        out = ingest({"name": "a1.bin", "digest": "d2", "family": "f",
                      "content": one_byte_variant(100), "size": len(BASE)})
        diff = out["differences"]
        self.assertEqual(diff["sample"], "a1.bin")
        self.assertEqual(diff["size_delta"], 0)
        self.assertEqual(diff["offsets"], [100])

    def test_content_free_samples_use_exact_hash_only(self):
        ingest({"name": "a.bin", "digest": "d1", "family": "f"})
        out = ingest({"name": "b.bin", "digest": "d2", "family": "f"})
        self.assertFalse(out["similar"])
        self.assertIsNone(out["differences"])

    def test_family_is_inferred_from_similar_content(self):
        ingest({"name": "a.bin", "digest": "d1", "family": "rat",
                "content": BASE})
        out = ingest({"name": "a1.bin", "digest": "d2",
                      "content": one_byte_variant(50)})
        self.assertEqual(out["family"], "rat")
        self.assertEqual(out["family_source"], "inferred")


class ThresholdTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_threshold_change_regroups_and_reports_affected(self):
        ingest({"name": "a.bin", "digest": "d1", "family": "f", "content": BASE})
        ingest({"name": "b.bin", "digest": "d2", "family": "f",
                "content": one_byte_variant(100)})
        self.assertEqual(family_view("f")["group_count"], 1)

        out = set_threshold("f", 0.999)
        self.assertEqual(out["group_count"], 2)
        self.assertTrue(out["affected_groups"])
        self.assertEqual(threshold("f"), 0.999)
        self.assertEqual(family_view("f")["group_count"], 2)

    def test_threshold_is_per_family(self):
        set_threshold("ransom", 0.99)
        self.assertEqual(threshold("ransom"), 0.99)
        self.assertEqual(threshold("rat"), 0.85)

    def test_invalid_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            set_threshold("f", 1.5)


class ReportTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_report_gives_dedupe_delta_and_groups(self):
        ingest({"name": "a.bin", "digest": "d1", "family": "f", "content": BASE})
        ingest({"name": "a-dup.bin", "digest": "d1", "family": "f",
                "content": BASE})
        ingest({"name": "a1.bin", "digest": "d2", "family": "f",
                "content": one_byte_variant(100)})
        out = report()
        self.assertEqual(out["dedupe"]["before"], 3)
        self.assertEqual(out["dedupe"]["after"], 1)
        self.assertEqual(out["dedupe"]["exact_duplicates"], 1)
        self.assertEqual(out["group_counts"], {"f": 1})
        self.assertEqual(out["similar_group_counts"], {"f": 1})
        self.assertEqual(out["diff_count"], 1)

    def test_report_can_filter_by_family(self):
        ingest({"name": "a.bin", "digest": "d1", "family": "f", "content": BASE})
        ingest({"name": "b.bin", "digest": "d2", "family": "g",
                "content": one_byte_variant(10)})
        out = report(family="g")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["families"], {"g": 1})

    def test_failed_samples_are_retained_with_reason(self):
        out = ingest({"name": "broken.bin"})
        self.assertEqual(out["status"], "failed")
        self.assertTrue(out["retained"])
        self.assertEqual(out["reason"], "missing_digest")
        self.assertEqual(len(failures()), 1)
        self.assertEqual(failures(reason="missing_digest")[0]["name"],
                         "broken.bin")
        self.assertEqual(failures(reason="invalid_size"), [])

    def test_same_digest_different_content_is_reported_not_swallowed(self):
        ingest({"name": "a.bin", "digest": "aa11", "family": "f",
                "content": BASE})
        out = ingest({"name": "b.bin", "digest": "aa11", "family": "f",
                      "content": BASE + b"x"})
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "digest_conflict")
        self.assertEqual(failures(reason="digest_conflict")[0]["name"], "b.bin")

    def test_report_counts_failures_and_filters_reason(self):
        ingest("not-a-dict")
        ingest({"name": "broken.bin"})
        ingest({"name": "big.bin", "digest": "d", "size": -1})
        out = report()
        self.assertEqual(out["failure_counts"],
                         {"invalid_sample": 1, "invalid_size": 1,
                          "missing_digest": 1})
        filtered = report(failure_reason="missing_digest")
        self.assertEqual(len(filtered["failures"]), 1)
        self.assertEqual(filtered["failures"][0]["reason"], "missing_digest")

    def test_retry_failed_keeps_still_broken_samples(self):
        ingest({"name": "broken.bin"})
        outcome = retry_failed()
        self.assertEqual(outcome["failed"], 1)
        self.assertEqual(len(failures()), 1)


class CompareTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_compare_reports_size_and_positions(self):
        ingest({"name": "a.bin", "digest": "d1", "family": "f",
                "content": BASE, "size": len(BASE)})
        ingest({"name": "b.bin", "digest": "d2", "family": "f",
                "content": BASE + b"tail", "size": len(BASE) + 4})
        out = compare("a.bin", "b.bin")
        self.assertEqual(out["differences"]["size_delta"], 4)
        self.assertEqual(out["differences"]["positions"][0]["offset"],
                         len(BASE))
        self.assertGreater(out["similarity"], 0)

    def test_group_view_lists_variants(self):
        ingest({"name": "a.bin", "digest": "d1", "family": "f", "content": BASE})
        ingest({"name": "b.bin", "digest": "d2", "family": "f",
                "content": one_byte_variant(7)})
        group = family_view("f")["groups"][0]
        detail = group_view("f", group["id"])
        self.assertEqual(detail["sample_count"], 2)
        self.assertEqual(len(detail["variants"]), 1)


if __name__ == "__main__":
    unittest.main()
