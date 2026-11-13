import json
import os
import shutil
import tempfile
import unittest

from partquery import (
    ROOT,
    InvalidFilterError,
    InvalidPartitionError,
    MissingPartitionError,
    PartitionReadError,
    execute_plan,
    plan_query,
    query,
)


class QueryTest(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)
        base = os.path.join(ROOT, "orders")
        os.makedirs(base)
        for day, city in (("2026-01-01", "sh"), ("2026-01-02", "bj")):
            with open(os.path.join(base, f"dt={day}.jsonl"), "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"city": city, "amount": 1}) + "\n")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_filters_rows(self):
        self.assertEqual(query("orders", {"city": "bj"})["rows"][0]["city"], "bj")

    def test_scans_every_partition(self):
        self.assertEqual(query("orders", {"city": "sh"})["scanned"], 2)

    def test_missing_dataset_silent(self):
        self.assertEqual(query("nope", {})["rows"], [])


class PartitionPruningTest(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)
        self.base = os.path.join(ROOT, "orders")
        os.makedirs(self.base)
        self.fixture = {
            "2026-01-31": [
                {"city": "sh", "amount": 5},
                {"city": "bj", "amount": 20},
            ],
            "2026-02-01": [
                {"city": "sh", "amount": 15},
                {"city": "bj", "amount": 25},
            ],
            "2026-02-15": [{"city": "sh", "amount": 30}],
            "2026-03-01": [{"city": "bj", "amount": 8}],
        }
        for day, rows in self.fixture.items():
            with open(os.path.join(self.base, f"dt={day}.jsonl"), "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_time_range_prunes_partitions(self):
        result = query("orders", {"dt": {"gte": "2026-02-01", "lt": "2026-03-01"}})
        self.assertEqual(
            result["partitions"],
            ["dt=2026-02-01.jsonl", "dt=2026-02-15.jsonl"],
        )
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["scanned_total"], 4)

    def test_range_boundaries_are_inclusive_and_exclusive(self):
        result = query("orders", {"dt": {"gte": "2026-02-01", "lte": "2026-02-15"}})
        self.assertEqual(
            result["partitions"],
            ["dt=2026-02-01.jsonl", "dt=2026-02-15.jsonl"],
        )
        exclusive = query("orders", {"dt": {"gt": "2026-02-01", "lt": "2026-03-01"}})
        self.assertEqual(exclusive["partitions"], ["dt=2026-02-15.jsonl"])
        with self.assertRaises(MissingPartitionError):
            query("orders", {"dt": {"gt": "2026-02-01", "lt": "2026-02-15"}})

    def test_not_equal_operator_on_partition(self):
        result = query("orders", {"dt": {"ne": "2026-02-01"}})
        self.assertNotIn("dt=2026-02-01.jsonl", result["partitions"])
        self.assertEqual(result["scanned"], 3)

    def test_partition_equality_prunes_to_one_file(self):
        result = query("orders", {"dt": "2026-03-01"})
        self.assertEqual(result["partitions"], ["dt=2026-03-01.jsonl"])
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["row_count"], 1)

    def test_row_filters_are_pushed_into_read(self):
        result = query("orders", {"city": "sh", "amount": {"gte": 10}})
        self.assertEqual(result["scanned"], 4)
        self.assertEqual(result["row_count"], 6)
        self.assertEqual([row["amount"] for row in result["rows"]], [15, 30])

    def test_pruning_and_pushdown_compose(self):
        result = query(
            "orders",
            {"dt": {"gte": "2026-02-01"}, "city": "sh"},
        )
        self.assertEqual(result["scanned"], 3)
        self.assertEqual([row["city"] for row in result["rows"]], ["sh", "sh"])
        self.assertEqual([row["amount"] for row in result["rows"]], [15, 30])

    def test_missing_partition_raises_instead_of_silence(self):
        with self.assertRaises(MissingPartitionError):
            query("orders", {"dt": "2026-12-31"})
        with self.assertRaises(MissingPartitionError):
            query("orders", {"dt": {"gt": "2026-12-01"}})

    def test_missing_dataset_with_partition_filter_raises(self):
        with self.assertRaises(MissingPartitionError):
            query("nope", {"dt": "2026-02-01"})

    def test_stats_report_partitions_rows_and_elapsed(self):
        result = query("orders", {"dt": {"gte": "2026-02-01"}})
        self.assertEqual(result["scanned"], 3)
        self.assertEqual(result["row_count"], 4)
        self.assertEqual(len(result["rows"]), 4)
        self.assertIsInstance(result["elapsed_ms"], float)
        self.assertGreaterEqual(result["elapsed_ms"], 0.0)

    def test_plan_is_json_serializable_and_replays(self):
        plan = plan_query("orders", {"dt": {"gte": "2026-02-01"}, "city": "bj"})
        restored = json.loads(json.dumps(plan))
        self.assertEqual(restored["partitions"], plan["partitions"])
        replayed = execute_plan(restored)
        direct = query("orders", {"dt": {"gte": "2026-02-01"}, "city": "bj"})
        self.assertEqual(replayed["rows"], direct["rows"])
        self.assertEqual(replayed["partitions"], direct["partitions"])

    def test_query_result_is_json_serializable(self):
        result = query("orders", {"dt": "2026-02-01"})
        json.dumps(result)

    def test_invalid_filter_operator_raises(self):
        with self.assertRaises(InvalidFilterError):
            query("orders", {"amount": {"between": (1, 2)}})
        with self.assertRaises(InvalidFilterError):
            query("orders", {"amount": {}})
        with self.assertRaises(InvalidFilterError):
            query("orders", [])

    def test_incomparable_row_value_raises(self):
        with open(os.path.join(self.base, "dt=2026-03-02.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"city": "gz", "amount": "x"}) + "\n")
        with self.assertRaises(InvalidFilterError):
            query("orders", {"amount": {"gte": 10}})

    def test_unparseable_partition_name_raises(self):
        with open(os.path.join(self.base, "broken.jsonl"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        with self.assertRaises(InvalidPartitionError):
            query("orders", {})

    def test_corrupt_jsonl_raises_partition_read_error(self):
        with open(os.path.join(self.base, "dt=2026-03-03.jsonl"), "w", encoding="utf-8") as handle:
            handle.write("{not json}\n")
        with self.assertRaises(PartitionReadError):
            query("orders", {"dt": "2026-03-03"})

    def test_multi_key_partition_pruning(self):
        multi = os.path.join(ROOT, "shipped")
        os.makedirs(multi)
        for name in ("dt=2026-02-01,city=sh", "dt=2026-02-01,city=bj", "dt=2026-03-01,city=sh"):
            with open(os.path.join(multi, name + ".jsonl"), "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"amount": 1}) + "\n")
        result = query("shipped", {"dt": "2026-02-01", "city": "sh"})
        self.assertEqual(result["partitions"], ["dt=2026-02-01,city=sh.jsonl"])
        self.assertEqual(result["scanned"], 1)

    def test_empty_dataset_without_partition_filter_still_empty(self):
        os.makedirs(os.path.join(ROOT, "empty"))
        result = query("empty", {})
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["scanned"], 0)
        self.assertEqual(result["row_count"], 0)
