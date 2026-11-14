import importlib
import json
import os
import tempfile
import unittest

from partitionmove import (
    DEFAULT_SOURCE,
    ValidationError,
    artifacts,
    move,
    progress,
    read,
    rollback,
    rows_at,
    seed,
    write,
)

module = importlib.import_module("partitionmove.move")


class MoveTest(unittest.TestCase):
    def setUp(self):
        module._moved.clear()
        self.state_dir = tempfile.mkdtemp(prefix="partitionmove-test-")

    def tearDown(self):
        module._states.pop(os.path.abspath(self.state_dir), None)

    def _seed(self, rows, path=DEFAULT_SOURCE, partition="p1"):
        seed(partition, rows, path=path, state_dir=self.state_dir)

    def _manifest(self):
        path = artifacts(state_dir=self.state_dir)["manifest"]
        if not os.path.exists(path):
            return {"partitions": {}}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    # -- 既有接口 ---------------------------------------------------------
    def test_move_records(self):
        self.assertEqual(move("p1", "cold")["target"], "cold")

    def test_move_counts(self):
        self.assertEqual(move("p1", "cold")["moved"], 1)

    def test_rollback_unsupported(self):
        with self.assertRaises(NotImplementedError):
            rollback()

    # -- 双写与位点 -------------------------------------------------------
    def test_move_double_writes_and_advances_cursor(self):
        self._seed([{"id": 1}, {"id": 2}])
        observed = {}

        def hook(info):
            result = write("p1", {"id": 3}, state_dir=self.state_dir)
            journal = progress("p1", state_dir=self.state_dir)
            observed["write"] = result
            observed["phase"] = journal["phase"]
            observed["cursor"] = journal["cursor"]

        result = move(
            "p1", "cold", state_dir=self.state_dir, hooks={"before_switch": hook}
        )

        self.assertTrue(observed["write"]["double_written"])
        self.assertEqual(observed["phase"], "catchup")
        self.assertEqual(observed["cursor"], 3)
        self.assertTrue(result["ok"])
        self.assertEqual(result["phases"], ["prepare", "copy", "catchup", "validate", "switch", "done"])
        self.assertEqual(result["cursor"], 3)
        self.assertEqual(read("p1", state_dir=self.state_dir), [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(rows_at("p1", "hot", state_dir=self.state_dir), read("p1", state_dir=self.state_dir))
        self.assertIsNone(progress("p1", state_dir=self.state_dir))

    def test_journal_and_manifest_persisted(self):
        self._seed([{"id": 1}])
        move("p1", "cold", state_dir=self.state_dir)
        manifest = self._manifest()
        entry = manifest["partitions"]["p1"]
        self.assertEqual(entry["state"], "switched")
        self.assertEqual(entry["active_path"], "cold")
        self.assertEqual(entry["source"], "hot")
        # 旧路径保留，未删除。
        self.assertEqual(rows_at("p1", "hot", state_dir=self.state_dir), [{"id": 1}])

    # -- 原子切换 ---------------------------------------------------------
    def test_switch_is_atomic_single_manifest_replace(self):
        self._seed([{"id": 1}])
        seen = {}

        def before(info):
            seen["during"] = self._manifest()["partitions"].get("p1")

        def after(info):
            seen["after"] = self._manifest()["partitions"]["p1"]

        result = move(
            "p1",
            "cold",
            state_dir=self.state_dir,
            hooks={"in_switch": before, "after_switch": after},
        )

        self.assertIsNone(seen["during"])  # 切换中读者只看到旧清单
        self.assertEqual(seen["after"]["state"], "switched")  # 完成后是完整新清单
        self.assertEqual(seen["after"]["active_path"], "cold")
        self.assertTrue(result["switch"]["ok"])
        self.assertEqual(result["switch"]["active_path"], "cold")

    def test_switch_failure_rolls_back_and_keeps_old_path(self):
        self._seed([{"id": 1}, {"id": 2}])

        def fail(info):
            raise RuntimeError("switch failed")

        result = move(
            "p1", "cold", state_dir=self.state_dir, hooks={"after_switch": fail}
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["phase"], "rollback")
        self.assertEqual(result["switch"]["error"], "switch failed")
        self.assertEqual(result["rollback"]["restored"], "hot")
        self.assertTrue(result["rollback"]["verified"])
        self.assertEqual(read("p1", state_dir=self.state_dir), [{"id": 1}, {"id": 2}])
        self.assertEqual(rows_at("p1", "hot", state_dir=self.state_dir), [{"id": 1}, {"id": 2}])
        entry = self._manifest()["partitions"]["p1"]
        self.assertEqual(entry["state"], "rolled_back")
        self.assertEqual(entry["active_path"], "hot")

    # -- 校验 -------------------------------------------------------------
    def test_validation_detects_checksum_mismatch_and_rolls_back(self):
        self._seed([{"id": 1}, {"id": 2}])

        def corrupt(info):
            state = module._get_state(self.state_dir)
            state.rows_ref("cold", "p1")[0] = {"id": 999}

        result = move(
            "p1", "cold", state_dir=self.state_dir, hooks={"in_switch": corrupt}
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["rolled_back"])
        self.assertFalse(result["validation"]["ok"])
        self.assertFalse(result["validation"]["checks"]["checksum"])
        self.assertTrue(result["validation"]["checks"]["rows"])
        self.assertEqual(read("p1", state_dir=self.state_dir), [{"id": 1}, {"id": 2}])
        self.assertEqual(self._manifest()["partitions"]["p1"]["active_path"], "hot")

    def test_validation_error_type(self):
        self._seed([{"id": 1}])
        with self.assertRaises(ValidationError):
            raise ValidationError("x", {})
        self.assertEqual(ValidationError("x", {"ok": False}).validation, {"ok": False})

    # -- 回滚 -------------------------------------------------------------
    def test_rollback_from_manifest_restores_and_verifies(self):
        self._seed([{"id": 1}, {"id": 2}])
        move("p1", "cold", state_dir=self.state_dir)
        self.assertEqual(read("p1", state_dir=self.state_dir)[0], {"id": 1})

        result = rollback(partition="p1", state_dir=self.state_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        item = result["rolled_back"][0]
        self.assertEqual(item["from"], "manifest")
        self.assertEqual(item["restored"], "hot")
        self.assertTrue(item["verified"])
        self.assertEqual(read("p1", state_dir=self.state_dir), [{"id": 1}, {"id": 2}])
        self.assertEqual(self._manifest()["partitions"]["p1"]["state"], "rolled_back")

    def test_rollback_replays_rows_written_after_switch(self):
        self._seed([{"id": 1}])
        move("p1", "cold", state_dir=self.state_dir)
        write("p1", {"id": 2}, state_dir=self.state_dir)  # 切换后只写新路径

        result = rollback(partition="p1", state_dir=self.state_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["rolled_back"][0]["verification"]["replayed"], 1)
        self.assertEqual(
            read("p1", state_dir=self.state_dir), [{"id": 1}, {"id": 2}]
        )

    def test_rollback_without_partition_rolls_back_all(self):
        self._seed([{"id": 1}], partition="p1")
        self._seed([{"id": 2}], partition="p2")
        move("p1", "cold", state_dir=self.state_dir)
        move("p2", "cold", state_dir=self.state_dir)

        result = rollback(state_dir=self.state_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(read("p1", state_dir=self.state_dir), [{"id": 1}])
        self.assertEqual(read("p2", state_dir=self.state_dir), [{"id": 2}])

    def test_rollback_before_switch_uses_journal(self):
        self._seed([{"id": 1}])

        # 未切换的迁移只留下 journal；旧路径从未删除，按 baseline 校验。
        journal = {
            "partition": "p1",
            "migration_id": "m1",
            "source": "hot",
            "target": "cold",
            "phase": "catchup",
            "cursor": 1,
            "source_rows": 1,
            "source_checksum": module._checksum([{"id": 1}]),
        }
        state = module._get_state(self.state_dir)
        state.journals["p1"] = journal
        state.save_journal("p1")

        result = rollback(partition="p1", state_dir=self.state_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["rolled_back"][0]["from"], "journal")
        self.assertIsNone(progress("p1", state_dir=self.state_dir))
        self.assertEqual(read("p1", state_dir=self.state_dir), [{"id": 1}])

    def test_move_rejects_concurrent_migration(self):
        self._seed([{"id": 1}])
        state = module._get_state(self.state_dir)
        state.journals["p1"] = {
            "partition": "p1",
            "migration_id": "m1",
            "source": "hot",
            "target": "warm",
            "phase": "copy",
        }
        with self.assertRaises(module.MigrationError):
            move("p1", "cold", state_dir=self.state_dir)

    def test_move_rejects_same_path(self):
        self._seed([{"id": 1}])
        with self.assertRaises(ValueError):
            move("p1", "hot", state_dir=self.state_dir)


if __name__ == "__main__":
    unittest.main()
