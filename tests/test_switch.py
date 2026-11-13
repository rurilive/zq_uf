import subprocess
import sys
import unittest
from pathlib import Path

from versionflow import (
    SwitchError,
    audit_log,
    cache_keys,
    current,
    default_pipeline,
    metadata,
    put_cache,
    register_dataset,
    reset,
    switch,
)


class SwitchTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_pointer_changes(self):
        switch("orders", "v1")
        self.assertEqual(current("orders"), "v1")

    def test_single_step(self):
        switch("orders", "v1")
        result = switch("orders", "v2")
        self.assertEqual(result["steps"], ["pointer"])

    def test_no_audit(self):
        result = switch("orders", "v1")
        self.assertNotIn("operator", result)


def _boom(ds, ctx):
    raise RuntimeError("注入失败")


def _fail_step(pipeline, name):
    for step in pipeline:
        if step.name == name:
            step.action = _boom
    return pipeline


class PipelineTest(unittest.TestCase):
    def setUp(self):
        reset()
        register_dataset("orders", versions=["v1", "v2"], current="v1")

    def test_step_order(self):
        result = switch("orders", "v2", operator="tester", reason="升级")
        self.assertEqual(
            [s["step"] for s in result["step_results"]],
            ["stop_writes", "switch_dir", "update_metadata", "clear_cache"],
        )
        self.assertTrue(all(s["status"] == "ok" for s in result["step_results"]))

    def test_final_version(self):
        result = switch("orders", "v2", operator="tester", reason="升级")
        self.assertEqual(result["final_version"], "v2")
        self.assertFalse(result["rolled_back"])
        self.assertEqual(current("orders"), "v2")

    def test_cache_cleared(self):
        put_cache("orders", "k1", 1)
        put_cache("orders", "k2", 2)
        result = switch("orders", "v2", operator="tester", reason="升级")
        self.assertEqual(cache_keys("orders"), [])
        clear = result["step_results"][-1]
        self.assertEqual(clear["step"], "clear_cache")
        self.assertIn("2", clear["detail"])

    def test_cache_keeps_target_version(self):
        put_cache("orders", "old", 1)
        put_cache("orders", "new", 2, version="v2")
        switch("orders", "v2", operator="tester", reason="升级")
        self.assertEqual(cache_keys("orders"), ["new"])

    def test_audit_records(self):
        switch("orders", "v2", operator="tester", reason="升级")
        entry = audit_log()[-1]
        self.assertEqual(entry["operator"], "tester")
        self.assertEqual(entry["reason"], "升级")
        self.assertEqual(entry["from"], "v1")
        self.assertEqual(entry["to"], "v2")
        self.assertEqual(entry["status"], "success")
        self.assertEqual(len(entry["steps"]), 4)

    def test_metadata_updated(self):
        switch("orders", "v2", operator="tester", reason="升级")
        meta = metadata("orders")
        self.assertEqual(meta["current_version"], "v2")
        self.assertEqual(meta["previous_version"], "v1")
        self.assertEqual(meta["operator"], "tester")

    def test_failure_rolls_back(self):
        put_cache("orders", "k1", 1)
        pipeline = _fail_step(default_pipeline(), "update_metadata")
        with self.assertRaises(SwitchError) as ctx:
            switch("orders", "v2", operator="tester", reason="升级", pipeline=pipeline)
        exc = ctx.exception
        self.assertEqual(exc.step, "update_metadata")
        self.assertIn("update_metadata", str(exc))
        self.assertEqual(current("orders"), "v1")
        self.assertEqual(metadata("orders")["current_version"], "v1")
        self.assertEqual(cache_keys("orders"), ["k1"])
        statuses = [s["status"] for s in exc.step_results]
        self.assertIn("failed", statuses)
        self.assertIn("rolled_back", statuses)
        entry = audit_log()[-1]
        self.assertEqual(entry["status"], "rolled_back")
        self.assertEqual(entry["to"], "v2")

    def test_failure_restores_cache(self):
        put_cache("orders", "k1", 1)
        pipeline = _fail_step(default_pipeline(), "clear_cache")
        with self.assertRaises(SwitchError):
            switch("orders", "v2", operator="tester", reason="升级", pipeline=pipeline)
        self.assertEqual(current("orders"), "v1")
        self.assertEqual(cache_keys("orders"), ["k1"])

    def test_failure_at_first_step(self):
        pipeline = _fail_step(default_pipeline(), "stop_writes")
        with self.assertRaises(SwitchError) as ctx:
            switch("orders", "v2", operator="tester", reason="升级", pipeline=pipeline)
        self.assertEqual(current("orders"), "v1")
        self.assertEqual(ctx.exception.step, "stop_writes")


class ValidationTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_same_version_rejected(self):
        register_dataset("orders", versions=["v1"], current="v1")
        with self.assertRaises(ValueError):
            switch("orders", "v1")

    def test_unknown_version_rejected(self):
        register_dataset("orders", versions=["v1", "v2"], current="v1")
        with self.assertRaises(ValueError) as ctx:
            switch("orders", "v9")
        self.assertIn("v9", str(ctx.exception))

    def test_blank_dataset_rejected(self):
        with self.assertRaises(ValueError):
            switch("", "v1")

    def test_blank_version_rejected(self):
        with self.assertRaises(ValueError):
            switch("orders", "  ")

    def test_illegal_name_rejected(self):
        with self.assertRaises(ValueError):
            switch("../evil", "v1")

    def test_unregistered_dataset_auto_created(self):
        result = switch("adhoc", "v1")
        self.assertEqual(result["final_version"], "v1")
        self.assertIsNone(result["from"])

    def test_cache_on_unknown_dataset_rejected(self):
        with self.assertRaises(ValueError):
            put_cache("ghost", "k", 1)
        with self.assertRaises(ValueError):
            cache_keys("ghost")


class DemoTest(unittest.TestCase):
    def test_demo_output(self):
        repo = Path(__file__).resolve().parent.parent
        proc = subprocess.run(
            [sys.executable, "demo.py"], cwd=repo, capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("final_version=v2", proc.stdout)
        self.assertIn("final_version=v1", proc.stdout)
        self.assertIn("step stop_writes: ok", proc.stdout)
        self.assertIn("step clear_cache: ok", proc.stdout)
        self.assertIn("error:", proc.stdout)
