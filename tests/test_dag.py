import unittest

from dagprop import DagError, hot_update, rerun, reset, run


def chain():
    return {
        "nodes": [
            {"name": "extract"},
            {"name": "transform", "deps": ["extract"]},
            {"name": "load", "deps": ["transform"]},
        ]
    }


def executor(log, fail=()):
    """按名字执行节点并记录顺序；``fail`` 中的节点抛错。"""

    def execute(name):
        log.append(name)
        if name in fail:
            raise RuntimeError(f"{name} exploded")
        return {"node": name, "no": len(log)}

    return execute


class DagTest(unittest.TestCase):
    def setUp(self):
        reset()

    # ---- 基础执行 ----

    def test_run_is_linear(self):
        self.assertEqual(run({"nodes": ["a", "b"]})["status"], {"a": "ok", "b": "ok"})

    def test_no_skip(self):
        self.assertEqual(run({"nodes": ["a"]})["skipped"], [])

    def test_rerun_named_node_reuses_untouched_nodes(self):
        run({"nodes": ["a", "b"]})
        result = rerun(["a"])
        self.assertEqual(result["rerun"], ["a"])
        self.assertEqual(result["reused"], ["b"])

    def test_rerun_single_skipped_node_recovers_chain(self):
        run(chain(), execute=executor([], fail={"extract"}))
        seen = []
        result = rerun(["load"], execute=executor(seen))
        # 只点名 load，但它缺失的上游会一并补跑，成功后 load 不再被跳过。
        self.assertEqual(seen, ["extract", "transform", "load"])
        self.assertEqual(result["status"]["load"], "ok")
        self.assertEqual(result["reused"], [])

    def test_runs_in_dependency_order(self):
        dag = {
            "nodes": [
                {"name": "load", "deps": ["transform"]},
                {"name": "transform", "deps": ["extract"]},
                {"name": "extract"},
            ]
        }
        seen = []
        result = run(dag, execute=seen.append)
        self.assertEqual(seen, ["extract", "transform", "load"])
        self.assertEqual(result["order"], ["extract", "transform", "load"])

    # ---- 失败传播 ----

    def test_upstream_failure_skips_downstream_with_reason(self):
        seen = []
        result = run(chain(), execute=executor(seen, fail={"extract"}))
        self.assertEqual(result["status"]["extract"], "failed")
        self.assertEqual(result["status"]["transform"], "skipped")
        self.assertEqual(result["status"]["load"], "skipped")
        self.assertEqual(result["skipped"], ["transform", "load"])
        self.assertIn("extract failed", result["skip_reasons"]["transform"])
        self.assertEqual(result["errors"]["extract"], "extract exploded")
        # 下游不执行，因此只有失败节点进入重跑清单。
        self.assertEqual(result["rerun"], ["extract"])
        self.assertFalse(result["ok"])

    def test_skip_reason_mentions_skipped_upstream(self):
        result = run(chain(), execute=executor([], fail={"extract"}))
        self.assertEqual(
            result["skip_reasons"]["load"], "upstream not ok: transform skipped"
        )

    def test_downstream_never_executes_on_failure(self):
        seen = []
        run(chain(), execute=executor(seen, fail={"extract"}))
        self.assertEqual(seen, ["extract"])

    def test_out_of_order_declaration_follows_deps(self):
        dag = {
            "nodes": [
                {"name": "load", "deps": ["transform"]},
                {"name": "transform", "deps": ["extract"]},
                {"name": "extract"},
            ]
        }
        seen = []
        result = run(dag, execute=executor(seen))
        self.assertEqual(seen, ["extract", "transform", "load"])
        self.assertEqual(result["order"], ["extract", "transform", "load"])

    # ---- 复用与重跑 ----

    def test_rerun_reuses_successful_nodes(self):
        dag = {
            "nodes": [
                {"name": "extract"},
                {"name": "transform", "deps": ["extract"]},
                {"name": "load", "deps": ["transform"]},
            ]
        }
        run(dag, execute=executor([], fail={"load"}))
        seen = []
        result = rerun(execute=executor(seen))
        self.assertEqual(seen, ["load"])
        self.assertEqual(result["rerun"], ["load"])
        self.assertEqual(result["reused"], ["extract", "transform"])
        self.assertEqual(result["status"]["load"], "ok")
        self.assertTrue(result["ok"])

    def test_rerun_retries_skipped_nodes(self):
        run(chain(), execute=executor([], fail={"extract"}))
        seen = []
        result = rerun(execute=executor(seen))
        self.assertEqual(seen, ["extract", "transform", "load"])
        self.assertEqual(result["skipped"], [])
        self.assertTrue(result["ok"])

    def test_rerun_without_failures_does_nothing(self):
        run(chain())
        result = rerun()
        self.assertEqual(result["rerun"], [])
        self.assertEqual(result["reused"], ["extract", "transform", "load"])

    def test_rerun_named_ok_node_keeps_rest_cached(self):
        result = run(
            {
                "nodes": [
                    {"name": "a"},
                    {"name": "b", "deps": ["a"]},
                ]
            }
        )
        self.assertTrue(result["ok"])
        # 点名重跑成功节点，只影响它自己和下游，其余仍然复用。
        again = rerun(["b"])
        self.assertEqual(again["rerun"], ["b"])
        self.assertEqual(again["reused"], ["a"])

    def test_rerun_unknown_node_is_error(self):
        run(chain())
        with self.assertRaises(DagError):
            rerun(["nope"])

    # ---- 依赖热更新 ----

    def test_hot_update_applies_and_affects_next_run(self):
        run(chain())
        update = hot_update({"nodes": [{"name": "report", "deps": ["load"]}]})
        self.assertTrue(update["ok"])
        self.assertTrue(update["applied"])
        self.assertEqual(update["added"], ["report"])
        self.assertEqual(update["changed"], [])
        self.assertIsNone(update["error"])
        result = run()
        self.assertIn("report", result["status"])
        self.assertEqual(result["reused"], ["extract", "transform", "load"])
        self.assertEqual(result["rerun"], ["report"])

    def test_hot_update_changed_dependency_recomputes_subtree(self):
        run(chain())
        update = hot_update({"deps": {"load": ["extract"]}})
        self.assertTrue(update["ok"])
        self.assertEqual(update["changed"], ["load"])
        result = run()
        self.assertEqual(result["reused"], ["extract", "transform"])
        self.assertEqual(result["rerun"], ["load"])

    def test_hot_update_validation_only_keeps_old_graph(self):
        run(chain())
        update = hot_update({"nodes": [{"name": "report", "deps": ["load"]}]}, apply=False)
        self.assertTrue(update["ok"])
        self.assertFalse(update["applied"])
        self.assertEqual(update["proposed"]["report"], ["load"])
        result = run()
        self.assertNotIn("report", result["status"])
        self.assertTrue(result["ok"])

    def test_hot_update_missing_dependency_rejected_old_graph_survives(self):
        run(chain())
        update = hot_update({"nodes": [{"name": "report", "deps": ["ghost"]}]})
        self.assertFalse(update["ok"])
        self.assertFalse(update["applied"])
        self.assertIn("ghost", update["error"])
        self.assertEqual(update["missing"], ["ghost"])
        # 旧图仍然可以继续调度。
        result = run()
        self.assertEqual(result["status"], {"extract": "ok", "transform": "ok", "load": "ok"})
        self.assertEqual(set(update["graph"]), {"extract", "transform", "load"})

    def test_hot_update_cycle_rejected(self):
        run(chain())
        update = hot_update({"deps": {"extract": ["load"]}})
        self.assertFalse(update["ok"])
        self.assertTrue(update["cycle"])
        self.assertTrue(run()["ok"])

    def test_hot_update_unknown_node_rejected(self):
        run(chain())
        update = hot_update({"deps": {"ghost": ["extract"]}})
        self.assertFalse(update["ok"])
        self.assertIn("ghost", update["error"])

    def test_hot_update_result_is_returned_in_run(self):
        run(chain())
        hot_update({"nodes": [{"name": "ghost-node", "deps": ["nope"]}]})
        result = run()
        self.assertFalse(result["update"]["valid"])
        self.assertIn("nope", result["update"]["error"])

    def test_hot_update_noop_keeps_cached_results(self):
        run(chain())
        update = hot_update({"nodes": [{"name": "transform", "deps": ["extract"]}]})
        self.assertTrue(update["ok"])
        self.assertFalse(update["updated"])
        self.assertEqual(update["version"], 1)
        result = run()
        self.assertEqual(result["rerun"], [])

    def test_rerun_after_failure_reuses_ok_branch(self):
        dag = {
            "nodes": [
                {"name": "extract"},
                {"name": "a", "deps": ["extract"]},
                {"name": "b", "deps": ["extract"]},
            ]
        }
        run(dag, execute=executor([], fail={"b"}))
        seen = []
        result = rerun(execute=executor(seen))
        self.assertEqual(seen, ["b"])
        self.assertEqual(result["reused"], ["extract", "a"])
        self.assertTrue(result["ok"])

    def test_new_graph_failure_keeps_previous_state(self):
        run(chain())
        with self.assertRaises(DagError):
            run({"nodes": [{"name": "orphan", "deps": ["missing"]}]})
        self.assertTrue(run()["ok"])


if __name__ == "__main__":
    unittest.main()
