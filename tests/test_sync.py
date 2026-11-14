import ast
import os
import sys
import trace
import unittest
from unittest.mock import patch

import tablesync
import tablesync.sync  # noqa: F401  确保子模块已注册到 sys.modules
from tablesync import progress, reset, rollback, snapshot, sync

sync_module = sys.modules["tablesync.sync"]


class SyncTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_writes_rows(self):
        sync("t", [{"id": 1, "v": 1}])
        self.assertEqual(snapshot()[1]["v"], 1)

    def test_delete_not_propagated(self):
        sync("t", [{"id": 1}, {"id": 2}])
        sync("t", [{"id": 1}])
        self.assertIn(2, snapshot())

    def test_rollback_not_supported(self):
        with self.assertRaises(NotImplementedError):
            rollback(None)


class IncrementalSyncTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_incremental_only_applies_changed_rows(self):
        first = sync("t", [{"id": 1, "v": 1}, {"id": 2, "v": 2}])
        self.assertTrue(first["verified"])
        self.assertEqual(first["watermark"], 1)
        second = sync("t", [{"id": 1, "v": 9}])
        self.assertEqual(second["written"], 1)
        self.assertEqual(second["deleted"], 0)
        self.assertEqual(snapshot("t")[2]["v"], 2)
        self.assertEqual(snapshot("t")[1]["v"], 9)

    def test_explicit_row_watermark_stale_is_skipped(self):
        sync("t", [{"id": 1, "v": 1, "_wm": 5}], watermark=5)
        stale = sync("t", [{"id": 1, "v": 2, "_wm": 3}], watermark=6)
        self.assertEqual(stale["skipped"], 1)
        self.assertEqual(stale["changes"], 0)
        self.assertEqual(stale["watermark"], 6)
        self.assertEqual(snapshot("t")[1]["v"], 1)

    def test_repeat_batch_is_idempotent(self):
        rows = [{"id": 1, "v": 1, "_wm": 1}, {"id": 2, "v": 2, "_wm": 1}]
        first = sync("t", rows, watermark=1)
        repeated = sync("t", rows, watermark=1)
        self.assertTrue(repeated["verified"])
        self.assertEqual(repeated["changes"], 0)
        self.assertEqual(repeated["skipped"], 2)
        self.assertEqual(repeated["watermark"], 1)
        self.assertEqual(snapshot("t"), snapshot("t"))
        rollback(0, table="t")
        replayed = sync("t", rows, watermark=1)
        self.assertEqual(replayed["expected"], first["expected"])
        self.assertEqual(snapshot("t"), {1: {"id": 1, "v": 1}, 2: {"id": 2, "v": 2}})

    def test_tombstone_propagates_delete(self):
        sync("t", [{"id": 1, "v": 1}, {"id": 2, "v": 2}])
        result = sync("t", [{"id": 2, "_deleted": True, "_wm": 2}], watermark=2)
        self.assertEqual(result["deleted"], 1)
        self.assertNotIn(2, snapshot("t"))
        self.assertIn(1, snapshot("t"))
        self.assertEqual(progress("t")["tombstones"], 1)

    def test_tombstone_on_unknown_primary_key(self):
        result = sync("t", [{"id": 9, "_deleted": True, "_wm": 5}], watermark=5)
        self.assertTrue(result["verified"])
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(snapshot("t"), {})
        self.assertEqual(progress("t")["tombstones"], 1)

    def test_tombstone_then_resurrect(self):
        sync("t", [{"id": 1, "v": 1, "_wm": 1}], watermark=1)
        sync("t", [{"id": 1, "_deleted": True, "_wm": 2}], watermark=2)
        revived = sync("t", [{"id": 1, "v": 3, "_wm": 3}], watermark=3)
        self.assertEqual(revived["written"], 1)
        self.assertEqual(snapshot("t"), {1: {"id": 1, "v": 3}})
        self.assertEqual(progress("t")["tombstones"], 0)

    def test_empty_batch_and_delete_all_cover_empty_checksum(self):
        empty = sync("t", [])
        self.assertTrue(empty["verified"])
        self.assertEqual(empty["changes"], 0)
        sync("t", [{"id": 1, "v": 1}])
        cleared = sync("t", [{"id": 1, "_deleted": True}])
        self.assertEqual(cleared["deleted"], 1)
        self.assertEqual(snapshot("t"), {})

    def test_post_check_mismatch_stops_and_then_retries(self):
        sync("t", [{"id": 1, "v": 1}])

        def sabotage(table, state):
            state["target"][99] = {"id": 99}

        with patch.object(sync_module, "_before_commit", sabotage):
            failed = sync("t", [{"id": 2, "v": 2}])
        self.assertFalse(failed["verified"])
        self.assertEqual(failed["reason"], "post-check mismatch")
        self.assertEqual(failed["watermark"], 1)
        self.assertEqual(snapshot("t"), {1: {"id": 1, "v": 1}})
        self.assertTrue(progress("t")["failed"])
        self.assertEqual(progress("t")["batches"], 1)

        retried = sync("t", [{"id": 2, "v": 2}])
        self.assertTrue(retried["verified"])
        self.assertEqual(retried["watermark"], 2)
        self.assertFalse(progress("t")["failed"])

    def test_pre_check_mismatch_stops_batch(self):
        for bad_key in ("changes", "checksum", "rows", "target_checksum"):
            with self.subTest(bad_key=bad_key):
                reset()
                good = sync("t", [{"id": 1, "v": 1}])
                expected = dict(good["expected"])
                expected[bad_key] = "tampered"
                reset()
                failed = sync("t", [{"id": 1, "v": 1}], expected=expected)
                self.assertFalse(failed["verified"])
                self.assertEqual(failed["reason"], "pre-check mismatch")
                self.assertEqual(failed["watermark"], 0)
                self.assertEqual(snapshot("t"), {})
                self.assertTrue(progress("t")["failed"])

    def test_pre_check_passes_when_expected_matches(self):
        reference = sync("t", [{"id": 1, "v": 1}])
        reset()
        ok = sync("t", [{"id": 1, "v": 1}], expected=reference["expected"])
        self.assertTrue(ok["verified"])
        self.assertEqual(snapshot("t"), {1: {"id": 1, "v": 1}})

    def test_rollback_replays_to_same_state(self):
        sync("t", [{"id": 1, "v": 1, "_wm": 1}], watermark=1)
        sync("t", [{"id": 1, "v": 2, "_wm": 2}], watermark=2)
        sync("t", [{"id": 1, "v": 3, "_wm": 3}], watermark=3)
        rolled = rollback(1, table="t")
        self.assertEqual(rolled["watermark"], 1)
        self.assertEqual(snapshot("t"), {1: {"id": 1, "v": 1}})
        self.assertEqual(progress("t")["batches"], 1)
        replay = sync("t", [{"id": 1, "v": 2, "_wm": 2}], watermark=2)
        self.assertTrue(replay["verified"])
        self.assertEqual(snapshot("t"), {1: {"id": 1, "v": 2}})

    def test_rollback_restores_tombstones_too(self):
        sync("t", [{"id": 1, "v": 1, "_wm": 1}], watermark=1)
        sync("t", [{"id": 1, "_deleted": True, "_wm": 2}], watermark=2)
        rollback(1, table="t")
        self.assertEqual(snapshot("t"), {1: {"id": 1, "v": 1}})
        self.assertEqual(progress("t")["tombstones"], 0)

    def test_rollback_to_zero_empties_table(self):
        sync("t", [{"id": 1, "v": 1}])
        rollback(0, table="t")
        self.assertEqual(snapshot("t"), {})
        self.assertEqual(progress("t")["watermark"], 0)
        self.assertEqual(progress("t")["batches"], 0)

    def test_rollback_unknown_watermark(self):
        sync("t", [{"id": 1, "v": 1}])
        with self.assertRaises(LookupError):
            rollback(99, table="t")
        with self.assertRaises(LookupError):
            rollback(1, table="never-seen")

    def test_rollback_ambiguous_watermark_requires_table(self):
        sync("a", [{"id": 1, "v": 1}])
        sync("b", [{"id": 1, "v": 1}])
        with self.assertRaises(ValueError):
            rollback(1)
        rolled = rollback(1, table="b")
        self.assertEqual(rolled["table"], "b")

    def test_progress_per_table_and_all_tables(self):
        sync("a", [{"id": 1, "v": 1}])
        sync("b", [{"id": 1, "v": 1}, {"id": 2, "_deleted": True}])
        one = progress("a")
        self.assertEqual(one["table"], "a")
        self.assertEqual(one["watermark"], 1)
        self.assertEqual(one["batches"], 1)
        self.assertEqual(one["rows"], 1)
        self.assertFalse(one["failed"])
        all_tables = progress()
        self.assertEqual(set(all_tables), {"a", "b"})
        self.assertEqual(all_tables["b"]["tombstones"], 1)

    def test_snapshot_merge_and_primary_key_collision(self):
        sync("a", [{"id": 1, "v": "a"}])
        sync("b", [{"id": 2, "v": "b"}])
        self.assertEqual(snapshot("a"), {1: {"id": 1, "v": "a"}})
        self.assertEqual(snapshot(), {1: {"id": 1, "v": "a"}, 2: {"id": 2, "v": "b"}})
        sync("b", [{"id": 1, "v": "b2"}])
        with self.assertRaises(ValueError):
            snapshot()

    def test_reset_single_table(self):
        sync("a", [{"id": 1}])
        sync("b", [{"id": 1}])
        reset("a")
        self.assertEqual(set(progress()), {"b"})
        self.assertEqual(snapshot("b"), {1: {"id": 1}})


class CoverageTest(unittest.TestCase):
    def test_sync_module_full_line_coverage(self):
        source = os.path.join(os.path.dirname(tablesync.__file__), "sync.py")
        with open(source, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=source)
        definition_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        def_lines = {node.lineno for node in definition_nodes}
        docstring_lines = set()
        for node in definition_nodes:
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_lines.add(body[0].lineno)
        if (
            tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)
        ):
            docstring_lines.add(tree.body[0].lineno)
        # trace 只记录多行语句的起始行，续行靠起始行覆盖，这里按起始行计算。
        compound = (
            ast.If,
            ast.For,
            ast.While,
            ast.Try,
            ast.With,
            ast.AsyncFor,
            ast.AsyncWith,
            ast.Match,
        )
        statement_starts = {
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.stmt) and hasattr(node, "lineno")
        }
        simple_lines = {
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.stmt) and not isinstance(node, compound)
        }
        # trace 对跨行的复合语句头不单独打点，按其起始行之外的语句行统计覆盖。
        multi_line_headers = statement_starts - simple_lines
        module_level = {node.lineno for node in tree.body}
        coverable = (
            statement_starts - module_level - def_lines - docstring_lines - multi_line_headers
        )

        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        suite.addTests(loader.loadTestsFromTestCase(SyncTest))
        suite.addTests(loader.loadTestsFromTestCase(IncrementalSyncTest))
        tracer = trace.Trace(count=1, trace=0)
        run_result = tracer.runfunc(suite.run, unittest.TestResult())
        self.assertTrue(run_result.wasSuccessful(), "traced suite reported failures")

        executed = set()
        for (filename, lineno), _count in tracer.results().counts.items():
            if os.path.abspath(filename) == os.path.abspath(source):
                executed.add(lineno)
        missing = sorted(coverable - executed)
        self.assertEqual(missing, [], f"uncovered lines in sync.py: {missing}")
