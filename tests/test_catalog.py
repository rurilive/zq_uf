import os
import shutil
import tempfile
import unittest

from partitioncatalog import catalog, list_partitions, register


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_register_creates_dir(self):
        path = register({"dataset": "orders", "name": "dt=2026-01-01"})
        self.assertTrue(os.path.isdir(path))

    def test_list_returns_names(self):
        register({"dataset": "orders", "name": "dt=2026-01-01"})
        self.assertEqual(list_partitions("orders"), ["dt=2026-01-01"])

    def test_no_metadata(self):
        register({"dataset": "orders", "name": "dt=2026-01-01"})
        self.assertEqual(os.listdir(os.path.join(catalog.ROOT, "orders", "dt=2026-01-01")), [])

    def test_missing_dataset_empty(self):
        self.assertEqual(list_partitions("nope"), [])


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.dir, ignore_errors=True)


def make_partition(name, start=None, end=None, rows=100, checksum="abc123"):
    partition = {"dataset": "orders", "name": name, "rows": rows, "checksum": checksum}
    if start is not None:
        partition["start"] = start
        partition["end"] = end
    return partition


class MetadataTest(BaseTest):
    def test_register_stores_metadata(self):
        register(make_partition("dt=2026-01-01", start="2026-01-01", end="2026-01-01", rows=42))
        record = catalog.get("orders", "dt=2026-01-01")
        self.assertEqual(record["start"], "2026-01-01")
        self.assertEqual(record["end"], "2026-01-01")
        self.assertEqual(record["rows"], 42)
        self.assertEqual(record["checksum"], "abc123")
        self.assertEqual(record["path"], os.path.join(catalog.ROOT, "orders", "dt=2026-01-01"))
        self.assertIn("updated_at", record)

    def test_register_without_optional_fields(self):
        register({"dataset": "orders", "name": "dt=2026-01-01"})
        record = catalog.get("orders", "dt=2026-01-01")
        self.assertIsNone(record["start"])
        self.assertIsNone(record["end"])
        self.assertIsNone(record["rows"])
        self.assertIsNone(record["checksum"])

    def test_reregister_updates_metadata(self):
        register(make_partition("dt=2026-01-01", rows=100))
        register(make_partition("dt=2026-01-01", rows=200, checksum="def456"))
        record = catalog.get("orders", "dt=2026-01-01")
        self.assertEqual(record["rows"], 200)
        self.assertEqual(record["checksum"], "def456")

    def test_get_missing_raises(self):
        with self.assertRaises(KeyError):
            catalog.get("orders", "dt=2099-01-01")


class RangeQueryTest(BaseTest):
    def setUp(self):
        super().setUp()
        register(make_partition("dt=2026-01-15", start="2026-01-15", end="2026-01-15"))
        register(make_partition("dt=2026-02-01", start="2026-02-01", end="2026-02-03"))
        register(make_partition("dt=2026-03-01", start="2026-03-01", end="2026-03-01"))
        register({"dataset": "orders", "name": "dt=unknown"})

    def test_range_filters(self):
        self.assertEqual(
            list_partitions("orders", start="2026-02-01", end="2026-02-28"),
            ["dt=2026-02-01"],
        )

    def test_range_boundaries_inclusive(self):
        self.assertEqual(
            list_partitions("orders", start="2026-02-03", end="2026-03-01"),
            ["dt=2026-02-01", "dt=2026-03-01"],
        )

    def test_range_overlap_partial(self):
        self.assertEqual(
            list_partitions("orders", start="2026-01-31", end="2026-02-02"),
            ["dt=2026-02-01"],
        )

    def test_range_no_match_empty(self):
        self.assertEqual(list_partitions("orders", start="2026-06-01", end="2026-06-30"), [])

    def test_partition_without_range_excluded_from_range_query(self):
        names = list_partitions("orders", start="2020-01-01", end="2030-01-01")
        self.assertNotIn("dt=unknown", names)
        self.assertEqual(len(names), 3)

    def test_no_range_returns_all(self):
        self.assertEqual(len(list_partitions("orders")), 4)

    def test_start_without_end_raises(self):
        with self.assertRaises(ValueError):
            list_partitions("orders", start="2026-01-01")

    def test_inverted_range_raises(self):
        with self.assertRaises(ValueError):
            list_partitions("orders", start="2026-02-01", end="2026-01-01")

    def test_invalid_query_date_raises(self):
        with self.assertRaises(ValueError):
            list_partitions("orders", start="2026-13-01", end="2026-13-31")


class EventLogTest(BaseTest):
    def test_register_logs_event(self):
        register(make_partition("dt=2026-01-01"))
        events = catalog.read_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "register")
        self.assertEqual(events[0]["dataset"], "orders")
        self.assertEqual(events[0]["name"], "dt=2026-01-01")
        self.assertIn("ts", events[0])

    def test_reregister_logs_update_with_changed_fields(self):
        register(make_partition("dt=2026-01-01", rows=100))
        register(make_partition("dt=2026-01-01", rows=200))
        events = catalog.read_events()
        self.assertEqual([e["event"] for e in events], ["register", "update"])
        self.assertEqual(events[1]["detail"]["changed"]["rows"], {"old": 100, "new": 200})

    def test_identical_reregister_logs_nothing(self):
        register(make_partition("dt=2026-01-01"))
        register(make_partition("dt=2026-01-01"))
        events = catalog.read_events()
        self.assertEqual([e["event"] for e in events], ["register"])

    def test_remove_logs_event(self):
        register(make_partition("dt=2026-01-01"))
        catalog.remove("orders", "dt=2026-01-01")
        events = catalog.read_events()
        self.assertEqual([e["event"] for e in events], ["register", "remove"])

    def test_read_events_empty(self):
        self.assertEqual(catalog.read_events(), [])


class RemoveTest(BaseTest):
    def test_remove_deletes_record_and_dir(self):
        path = register(make_partition("dt=2026-01-01"))
        catalog.remove("orders", "dt=2026-01-01")
        self.assertEqual(list_partitions("orders"), [])
        self.assertFalse(os.path.exists(path))

    def test_remove_missing_raises(self):
        with self.assertRaises(KeyError):
            catalog.remove("orders", "dt=2099-01-01")


class RepairTest(BaseTest):
    def test_init_repairs_missing_records(self):
        register(make_partition("dt=2026-01-01", rows=10))
        os.makedirs(os.path.join(catalog.ROOT, "orders", "dt=2026-01-02"))
        repaired = catalog.init()
        self.assertEqual(repaired, [("orders", "dt=2026-01-02")])
        self.assertEqual(list_partitions("orders"), ["dt=2026-01-01", "dt=2026-01-02"])
        record = catalog.get("orders", "dt=2026-01-02")
        self.assertIsNone(record["rows"])

    def test_init_logs_repair_event(self):
        os.makedirs(os.path.join(catalog.ROOT, "orders", "dt=2026-01-02"))
        catalog.init()
        events = catalog.read_events()
        self.assertEqual([e["event"] for e in events], ["repair"])
        self.assertEqual(events[0]["name"], "dt=2026-01-02")

    def test_init_keeps_existing_records(self):
        register(make_partition("dt=2026-01-01", rows=10))
        os.makedirs(os.path.join(catalog.ROOT, "orders", "dt=2026-01-02"))
        catalog.init()
        self.assertEqual(catalog.get("orders", "dt=2026-01-01")["rows"], 10)

    def test_init_without_root(self):
        self.assertEqual(catalog.init(), [])

    def test_init_idempotent(self):
        os.makedirs(os.path.join(catalog.ROOT, "orders", "dt=2026-01-02"))
        catalog.init()
        self.assertEqual(catalog.init(), [])
        self.assertEqual(len(catalog.read_events()), 1)


class ValidationTest(BaseTest):
    def test_missing_dataset_raises(self):
        with self.assertRaises(ValueError):
            register({"name": "dt=2026-01-01"})

    def test_missing_name_raises(self):
        with self.assertRaises(ValueError):
            register({"dataset": "orders"})

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            register("orders/dt=2026-01-01")

    def test_path_traversal_name_raises(self):
        for bad in ("../evil", "a/b", ".", "..", ".hidden"):
            with self.assertRaises(ValueError, msg=bad):
                register({"dataset": "orders", "name": bad})

    def test_path_traversal_dataset_raises(self):
        with self.assertRaises(ValueError):
            register({"dataset": "../evil", "name": "dt=2026-01-01"})

    def test_negative_rows_raises(self):
        with self.assertRaises(ValueError):
            register(make_partition("dt=2026-01-01", rows=-1))

    def test_non_int_rows_raises(self):
        with self.assertRaises(ValueError):
            register(make_partition("dt=2026-01-01", rows="100"))
        with self.assertRaises(ValueError):
            register(make_partition("dt=2026-01-01", rows=True))

    def test_zero_rows_ok(self):
        register(make_partition("dt=2026-01-01", rows=0))
        self.assertEqual(catalog.get("orders", "dt=2026-01-01")["rows"], 0)

    def test_empty_checksum_raises(self):
        with self.assertRaises(ValueError):
            register(make_partition("dt=2026-01-01", checksum=""))

    def test_start_without_end_raises(self):
        with self.assertRaises(ValueError):
            register({"dataset": "orders", "name": "dt=2026-01-01", "start": "2026-01-01"})

    def test_inverted_partition_range_raises(self):
        with self.assertRaises(ValueError):
            register(make_partition("dt=2026-01-01", start="2026-02-01", end="2026-01-01"))

    def test_invalid_partition_date_raises(self):
        with self.assertRaises(ValueError):
            register(make_partition("dt=2026-01-01", start="2026-13-01", end="2026-13-01"))
        with self.assertRaises(ValueError):
            register(make_partition("dt=2026-01-01", start=20260101, end=20260101))


class DemoTest(unittest.TestCase):
    def test_demo_output(self):
        import subprocess
        import sys

        demo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo.py")
        result = subprocess.run(
            [sys.executable, demo], capture_output=True, text=True, check=True,
        )
        lines = result.stdout.strip().splitlines()
        self.assertEqual(lines[0], "['dt=2026-01-01', 'dt=2026-01-02']")
        self.assertEqual(lines[1], "40")
        self.assertEqual(lines[2], "28 dt=2026-02-01 dt=2026-02-28")
        self.assertEqual(lines[3], "repaired: [('metrics', 'dt=2026-03-09')]")
        self.assertEqual(lines[4], "events: 44 ['register', 'update', 'repair']")

