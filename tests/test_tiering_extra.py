import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tiering import TieringError, apply, classify, plan, record_access
from tiering import tier

NOW = 1_700_000_000.0
DAY = tier.SECONDS_PER_DAY


def _write(path, content):
    with open(path, "w") as fh:
        fh.write(content)


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_record_access_accumulates(self):
        record_access(self.dir, "a.parquet", hits=3, at=NOW - 5 * DAY)
        entry = record_access(self.dir, "a.parquet", hits=2, at=NOW)
        self.assertEqual(entry, {"hits": 5, "last_access": NOW})

    def test_stats_persisted_as_json(self):
        record_access(self.dir, "a.parquet", hits=1, at=NOW)
        with open(os.path.join(self.dir, tier.STATS_FILE)) as fh:
            self.assertEqual(json.load(fh)["a.parquet"]["hits"], 1)

    def test_record_access_rejects_bad_args(self):
        with self.assertRaises(TieringError):
            record_access(self.dir, "a.parquet", hits=0)
        with self.assertRaises(TieringError):
            record_access(self.dir, "../escape.parquet")
        with self.assertRaises(TieringError):
            record_access(os.path.join(self.dir, "missing"), "a.parquet")


class ClassifyTest(unittest.TestCase):
    def test_boundaries(self):
        hot_idle = NOW - tier.HOT_MAX_IDLE_DAYS * DAY
        self.assertEqual(classify(tier.HOT_MIN_HITS, hot_idle, NOW), "hot")
        self.assertEqual(classify(tier.HOT_MIN_HITS, hot_idle - DAY, NOW), "warm")
        self.assertEqual(classify(tier.COLD_MAX_HITS, NOW, NOW), "cold")
        cold_idle = NOW - tier.COLD_MIN_IDLE_DAYS * DAY
        self.assertEqual(classify(99, cold_idle, NOW), "cold")
        self.assertEqual(classify(5, NOW - 30 * DAY, NOW), "warm")


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for name in ("hot.parquet", "warm.parquet", "cold.parquet"):
            _write(os.path.join(self.dir, name), "x" * 100)
        record_access(self.dir, "hot.parquet", hits=20, at=NOW - DAY)
        record_access(self.dir, "warm.parquet", hits=5, at=NOW - 30 * DAY)
        record_access(self.dir, "cold.parquet", hits=1, at=NOW - 200 * DAY)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def tiers(self, payload):
        return {m["name"]: m["tier"] for m in payload["moves"]}

    def test_three_tier_plan(self):
        self.assertEqual(self.tiers(plan(self.dir, now=NOW)), {
            "hot.parquet": "hot", "warm.parquet": "warm", "cold.parquet": "cold"})

    def test_plan_is_json_serializable_and_restartable(self):
        first = plan(self.dir, now=NOW)
        json.dumps(first)
        self.assertEqual(plan(self.dir, now=NOW), first)

    def test_plan_missing_dataset_raises(self):
        with self.assertRaises(TieringError):
            plan(os.path.join(self.dir, "nope"))

    def test_corrupt_stats_raises(self):
        with open(os.path.join(self.dir, tier.STATS_FILE), "w") as fh:
            fh.write("{not json")
        with self.assertRaises(TieringError):
            plan(self.dir, now=NOW)


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for name in ("hot.parquet", "warm.parquet", "cold.parquet"):
            _write(os.path.join(self.dir, name), "x" * 100)
        record_access(self.dir, "hot.parquet", hits=20, at=NOW - DAY)
        record_access(self.dir, "warm.parquet", hits=5, at=NOW - 30 * DAY)
        record_access(self.dir, "cold.parquet", hits=1, at=NOW - 200 * DAY)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_dry_run_moves_nothing(self):
        result = apply(plan(self.dir, dry_run=True, now=NOW))
        self.assertEqual(
            (result["moved"], result["would_move"], result["freed_bytes"]),
            (0, 2, 200))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "warm.parquet")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "warm")))

    def test_apply_relocates_and_validates(self):
        result = apply(plan(self.dir, now=NOW))
        self.assertEqual(
            (result["moved"], result["skipped"], result["freed_bytes"]),
            (2, 1, 200))
        for name, sub in (("warm.parquet", "warm"), ("cold.parquet", "cold")):
            dst = os.path.join(self.dir, sub, name)
            with open(dst) as fh:
                self.assertEqual(fh.read(), "x" * 100)
            self.assertFalse(os.path.exists(os.path.join(self.dir, name)))
        with open(os.path.join(self.dir, tier.CATALOG_FILE)) as fh:
            catalog = json.load(fh)
        self.assertEqual(catalog["warm.parquet"]["tier"], "warm")
        self.assertEqual(catalog["cold.parquet"]["tier"], "cold")

    def test_apply_missing_source_raises(self):
        payload = plan(self.dir, now=NOW)
        os.remove(os.path.join(self.dir, "warm.parquet"))
        with self.assertRaises(TieringError):
            apply(payload)

    def test_apply_existing_destination_raises(self):
        payload = plan(self.dir, now=NOW)
        os.makedirs(os.path.join(self.dir, "warm"))
        _write(os.path.join(self.dir, "warm", "warm.parquet"), "y")
        with self.assertRaises(TieringError):
            apply(payload)

    def test_apply_rejects_bad_payload(self):
        with self.assertRaises(TieringError):
            apply({})
        with self.assertRaises(TieringError):
            apply({"dataset": self.dir, "moves": [{"path": "x"}]})


class DemoTest(unittest.TestCase):
    def test_demo_output_matches_expectations(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run(
            [sys.executable, "demo.py"], cwd=root,
            capture_output=True, text=True, check=True).stdout
        self.assertIn("'would_move': 4", out)
        self.assertIn("'moved': 4", out)
        self.assertIn("'freed_bytes': 400", out)
