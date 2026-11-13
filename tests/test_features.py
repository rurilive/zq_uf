import unittest

from reconcheck import compare


class CompositeKeyTest(unittest.TestCase):
    def test_composite_key_match(self):
        left = [{"order": 1, "line": "a", "amount": 10},
                {"order": 1, "line": "b", "amount": 20}]
        right = [{"order": 1, "line": "a", "amount": 10},
                 {"order": 1, "line": "c", "amount": 30}]
        result = compare(left, right, {"keys": ["order", "line"]})
        self.assertEqual(result["left_only"], [(1, "b")])
        self.assertEqual(result["right_only"], [(1, "c")])
        self.assertEqual(result["mismatches"], [])

    def test_composite_key_field_mismatch(self):
        left = [{"order": 1, "line": "a", "amount": 10}]
        right = [{"order": 1, "line": "a", "amount": 11}]
        result = compare(left, right, {"keys": ["order", "line"]})
        self.assertEqual(result["mismatches"],
                         [{"key": (1, "a"), "field": "amount",
                           "left": 10, "right": 11}])
        self.assertEqual(result["status"], "fail")


class ToleranceTest(unittest.TestCase):
    def test_abs_tolerance(self):
        left = [{"id": 1, "amount": 100.00}]
        right = [{"id": 1, "amount": 100.004}]
        result = compare(left, right, {"tolerance": {"abs": 0.01}})
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["status"], "pass")

    def test_abs_tolerance_exceeded(self):
        left = [{"id": 1, "amount": 100.00}]
        right = [{"id": 1, "amount": 100.02}]
        result = compare(left, right, {"tolerance": {"abs": 0.01}})
        self.assertEqual(len(result["mismatches"]), 1)

    def test_rel_tolerance(self):
        left = [{"id": 1, "amount": 1000.0}]
        right = [{"id": 1, "amount": 1001.0}]
        result = compare(left, right, {"tolerance": {"rel": 0.002}})
        self.assertEqual(result["mismatches"], [])

    def test_field_tolerance_override(self):
        left = [{"id": 1, "amount": 100.4, "qty": 10}]
        right = [{"id": 1, "amount": 100.0, "qty": 10.2}]
        config = {"tolerance": {"abs": 0.0},
                  "field_tolerances": {"amount": {"abs": 0.5}}}
        result = compare(left, right, config)
        self.assertEqual([d["field"] for d in result["mismatches"]], ["qty"])


class DuplicateKeyTest(unittest.TestCase):
    def test_duplicate_left(self):
        left = [{"id": 1, "amount": 10}, {"id": 1, "amount": 10}]
        right = [{"id": 1, "amount": 10}]
        result = compare(left, right)
        self.assertEqual(result["duplicate_keys"]["left"], [1])
        self.assertEqual(result["duplicate_keys"]["right"], [])
        self.assertEqual(result["status"], "fail")

    def test_duplicate_not_silently_matched(self):
        left = [{"id": 1, "amount": 10}, {"id": 1, "amount": 99}]
        right = [{"id": 1, "amount": 10}]
        result = compare(left, right)
        self.assertEqual(result["duplicate_keys"]["left"], [1])
        self.assertEqual(result["mismatches"], [])


class PartitionTest(unittest.TestCase):
    LEFT = [{"id": 1, "region": "cn", "amount": 10},
            {"id": 2, "region": "cn", "amount": 20},
            {"id": 3, "region": "us", "amount": 30}]
    RIGHT = [{"id": 1, "region": "cn", "amount": 10},
             {"id": 2, "region": "cn", "amount": 21},
             {"id": 4, "region": "eu", "amount": 40}]

    def test_missing_partitions(self):
        result = compare(self.LEFT, self.RIGHT, {"partition_by": "region"})
        self.assertEqual(result["missing_partitions"],
                         {"left_only": ["eu"], "right_only": ["us"]})
        self.assertEqual(result["left_only"], [3])
        self.assertEqual(result["right_only"], [4])
        self.assertEqual(result["status"], "fail")

    def test_common_partition_mismatch(self):
        result = compare(self.LEFT, self.RIGHT, {"partition_by": "region"})
        self.assertEqual(result["mismatches"],
                         [{"key": 2, "field": "amount", "left": 20, "right": 21}])

    def test_partitioned_stream(self):
        config = {"partition_by": "region", "stream_chunk_size": 1, "workers": 2}
        result = compare(iter(self.LEFT), iter(self.RIGHT), config)
        self.assertEqual(result["rows_left"], 3)
        self.assertEqual(result["rows_right"], 3)
        self.assertEqual(result["missing_partitions"]["left_only"], ["eu"])


class StreamMergeTest(unittest.TestCase):
    def test_generator_inputs(self):
        left = ({"id": i, "amount": i * 10} for i in range(1, 8))
        right = ({"id": i, "amount": i * 10} for i in range(3, 10))
        result = compare(left, right, {"stream_chunk_size": 2})
        self.assertEqual(result["left_only"], [1, 2])
        self.assertEqual(result["right_only"], [8, 9])
        self.assertEqual(result["mismatches"], [])
        self.assertEqual((result["rows_left"], result["rows_right"]), (7, 7))

    def test_unsorted_stream_with_mismatch(self):
        left = iter([{"id": 3, "amount": 30}, {"id": 1, "amount": 10},
                     {"id": 2, "amount": 20}, {"id": 4, "amount": 40},
                     {"id": 5, "amount": 50}])
        right = iter([{"id": 2, "amount": 20}, {"id": 5, "amount": 55},
                      {"id": 1, "amount": 10}, {"id": 4, "amount": 40},
                      {"id": 3, "amount": 30}])
        result = compare(left, right, {"stream_chunk_size": 2})
        self.assertEqual(result["mismatches"],
                         [{"key": 5, "field": "amount", "left": 50, "right": 55}])


class ExceptionRuleTest(unittest.TestCase):
    def test_field_rule_downgrades_to_warning(self):
        left = [{"id": 1, "amount": 10, "note": "a"}]
        right = [{"id": 1, "amount": 10, "note": "b"}]
        result = compare(left, right, {"exceptions": [{"fields": ["note"]}]})
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["warnings"],
                         [{"type": "mismatch", "key": 1, "field": "note",
                           "left": "a", "right": "b"}])
        self.assertEqual(result["status"], "pass")

    def test_max_abs_rule(self):
        left = [{"id": 1, "amount": 100.0}]
        right = [{"id": 1, "amount": 100.05}]
        config = {"exceptions": [{"fields": ["amount"], "max_abs": 0.1}]}
        result = compare(left, right, config)
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(result["status"], "pass")

    def test_key_rule_on_missing_row(self):
        left = [{"id": 1}, {"id": 2}]
        right = [{"id": 1}]
        result = compare(left, right, {"exceptions": [{"keys": [2]}]})
        self.assertEqual(result["left_only"], [])
        self.assertEqual(result["warnings"], [{"type": "left_only", "key": 2}])
        self.assertEqual(result["status"], "pass")

    def test_rule_not_hit_stays_error(self):
        left = [{"id": 1, "amount": 100.0}]
        right = [{"id": 1, "amount": 100.5}]
        config = {"exceptions": [{"fields": ["amount"], "max_abs": 0.1}]}
        result = compare(left, right, config)
        self.assertEqual(len(result["mismatches"]), 1)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["status"], "fail")


if __name__ == "__main__":
    unittest.main()
