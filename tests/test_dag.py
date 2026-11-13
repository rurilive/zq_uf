"""任务图编排的新增用例：依赖执行、局部重跑、上下文、站点覆盖与失败分支。

已有的 tests/test_flow.py 保持不动，这里只追加覆盖。
"""

import json
import os
import subprocess
import sys
import unittest

from dagcrawl import FlowError, for_site, register, reset_state, run

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXPECTED_DEMO_OUTPUT = (
    '{"params": {"limit": 2, "min_len": 7}, '
    '"state": {"clean": "done", "detail": "done", "enrich": "done", '
    '"list": "done", "store": "done"}, "stored": ["ITEM-0", "ITEM-1"]}'
)


def make_step(name, calls, output=None):
    def step(ctx):
        calls.append(name)
        ctx[name] = output if output is not None else name
        return ctx

    return step


class DependencyOrderTest(unittest.TestCase):
    def test_declared_out_of_order_runs_by_dependency(self):
        calls = []
        flow = {"steps": [
            {"name": "store", "fn": make_step("store", calls), "needs": ["clean"]},
            {"name": "clean", "fn": make_step("clean", calls), "needs": ["detail"]},
            {"name": "detail", "fn": make_step("detail", calls), "needs": ["list"]},
            {"name": "list", "fn": make_step("list", calls)},
        ]}
        run(flow, {})
        self.assertEqual(calls, ["list", "detail", "clean", "store"])

    def test_diamond_branches_both_run_before_join(self):
        calls = []
        flow = {"steps": [
            {"name": "list", "fn": make_step("list", calls)},
            {"name": "clean", "fn": make_step("clean", calls), "needs": ["list"]},
            {"name": "enrich", "fn": make_step("enrich", calls), "needs": ["list"]},
            {"name": "store", "fn": make_step("store", calls),
             "needs": ["clean", "enrich"]},
        ]}
        run(flow, {})
        self.assertEqual(calls, ["list", "clean", "enrich", "store"])

    def test_same_layer_order_is_deterministic(self):
        flow = {"steps": [
            {"name": "b", "fn": make_step("b", [])},
            {"name": "a", "fn": make_step("a", [])},
        ]}
        first = []
        run(flow, {})
        for step in flow["steps"]:
            step["fn"] = make_step(step["name"], first)
        run(reset_state(flow), {})
        again = []
        for step in flow["steps"]:
            step["fn"] = make_step(step["name"], again)
        run(reset_state(flow), {})
        self.assertEqual(first, ["b", "a"])
        self.assertEqual(again, first)


class PartialRerunTest(unittest.TestCase):
    def build_flow(self, calls, broken):
        def boom(ctx):
            calls.append("detail")
            if broken[0]:
                raise ValueError("detail failed")
            ctx["detail"] = True
            return ctx

        return {"steps": [
            {"name": "list", "fn": make_step("list", calls)},
            {"name": "detail", "fn": boom, "needs": ["list"]},
            {"name": "clean", "fn": make_step("clean", calls), "needs": ["detail"]},
            {"name": "store", "fn": make_step("store", calls), "needs": ["clean"]},
        ]}

    def test_only_failed_step_and_downstream_rerun(self):
        calls = []
        broken = [True]
        flow = self.build_flow(calls, broken)
        with self.assertRaises(ValueError):
            run(flow, {})
        self.assertEqual(calls, ["list", "detail"])
        self.assertEqual(flow["state"],
                         {"list": "done", "detail": "failed"})

        broken[0] = False
        ctx = run(flow, {})
        self.assertEqual(calls, ["list", "detail", "detail", "clean", "store"])
        self.assertTrue(ctx["store"])
        self.assertEqual(set(flow["state"].values()), {"done"})

    def test_state_and_context_survive_json_roundtrip(self):
        calls = []
        broken = [True]

        @register("json_list")
        def json_list(ctx):
            calls.append("list")
            return {**ctx, "items": ["x"]}

        @register("json_detail")
        def json_detail(ctx):
            calls.append("detail")
            if broken[0]:
                raise ValueError("detail failed")
            return {**ctx, "details": len(ctx["items"])}

        flow = {"steps": [
            {"name": "list", "fn": "json_list", "provides": ["items"]},
            {"name": "detail", "fn": "json_detail", "needs": ["list"],
             "provides": ["details"]},
        ]}
        ctx = {}
        with self.assertRaises(ValueError):
            run(flow, ctx)
        # 状态与上下文都是纯 JSON，可落盘后在“新进程”里恢复。
        flow2 = json.loads(json.dumps(flow))
        ctx2 = json.loads(json.dumps(ctx))
        broken[0] = False
        run(flow2, ctx2)
        self.assertEqual(calls, ["list", "detail", "detail"])
        self.assertEqual(ctx2["details"], 1)
        self.assertEqual(flow2["state"], {"list": "done", "detail": "done"})


class ContextAndParamsTest(unittest.TestCase):
    def test_context_passed_explicitly_between_steps(self):
        def produce(ctx):
            return {**ctx, "items": [1, 2]}

        def consume(ctx):
            return {**ctx, "total": sum(ctx["items"])}

        flow = {"steps": [
            {"name": "produce", "fn": produce, "provides": ["items"]},
            {"name": "consume", "fn": consume, "needs": ["produce"]},
        ]}
        ctx = run(flow, {})
        self.assertEqual(ctx["total"], 3)

    def test_params_precedence_flow_then_site_then_context(self):
        flow = {
            "params": {"limit": 10, "min_len": 6},
            "sites": {"site_b": {"limit": 3}},
            "site": "site_b",
            "steps": [{"name": "noop", "fn": lambda c: c}],
        }
        ctx = run(flow, {"params": {"min_len": 9}})
        self.assertEqual(ctx["params"], {"limit": 3, "min_len": 9})

    def test_for_site_returns_copy_and_keeps_original(self):
        flow = {
            "params": {"limit": 10},
            "sites": {"site_b": {"limit": 3}},
            "steps": [{"name": "noop", "fn": lambda c: c}],
        }
        clone = for_site(flow, "site_b")
        self.assertNotIn("site", flow)
        ctx = run(clone, {})
        self.assertEqual(ctx["params"]["limit"], 3)


class FailureBranchTest(unittest.TestCase):
    def test_cycle_raises_clear_error(self):
        flow = {"steps": [
            {"name": "a", "fn": lambda c: c, "needs": ["b"]},
            {"name": "b", "fn": lambda c: c, "needs": ["a"]},
        ]}
        with self.assertRaisesRegex(FlowError, "cycle"):
            run(flow, {})

    def test_unknown_dependency_raises(self):
        flow = {"steps": [
            {"name": "a", "fn": lambda c: c, "needs": ["ghost"]},
        ]}
        with self.assertRaisesRegex(FlowError, "unknown step 'ghost'"):
            run(flow, {})

    def test_unregistered_function_name_raises(self):
        flow = {"steps": [{"name": "a", "fn": "not_registered"}]}
        with self.assertRaisesRegex(FlowError, "unregistered function"):
            run(flow, {})

    def test_duplicate_step_name_raises(self):
        flow = {"steps": [
            {"name": "a", "fn": lambda c: c},
            {"name": "a", "fn": lambda c: c},
        ]}
        with self.assertRaisesRegex(FlowError, "duplicate step name"):
            run(flow, {})

    def test_missing_declared_artifact_raises(self):
        flow = {"steps": [
            {"name": "list", "fn": lambda c: c, "provides": ["items"]},
        ]}
        with self.assertRaisesRegex(FlowError, "did not produce"):
            run(flow, {})
        self.assertEqual(flow["state"], {"list": "failed"})

    def test_step_returning_none_raises(self):
        flow = {"steps": [{"name": "bad", "fn": lambda c: None}]}
        with self.assertRaisesRegex(RuntimeError, "returned None"):
            run(flow, {})

    def test_unknown_site_raises(self):
        flow = {
            "site": "nowhere",
            "sites": {"site_a": {}},
            "steps": [{"name": "noop", "fn": lambda c: c}],
        }
        with self.assertRaisesRegex(FlowError, "unknown site"):
            run(flow, {})

    def test_unknown_step_key_raises(self):
        flow = {"steps": [{"name": "a", "fn": lambda c: c, "need": []}]}
        with self.assertRaisesRegex(FlowError, "unknown keys"):
            run(flow, {})

    def test_empty_steps_raises(self):
        with self.assertRaisesRegex(FlowError, "non-empty list"):
            run({"steps": []}, {})

    def test_original_exception_type_propagates(self):
        def boom(ctx):
            raise KeyError("missing field")

        flow = {"steps": [{"name": "detail", "fn": boom}]}
        with self.assertRaises(KeyError):
            run(flow, {})
        self.assertEqual(flow["state"], {"detail": "failed"})


class DemoOutputTest(unittest.TestCase):
    def test_demo_output_matches_assertion(self):
        proc = subprocess.run(
            [sys.executable, "demo.py"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertEqual(proc.stdout.strip(), EXPECTED_DEMO_OUTPUT)


if __name__ == "__main__":
    unittest.main()

