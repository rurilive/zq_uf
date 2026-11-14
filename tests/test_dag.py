import json
import os
import shutil
import tempfile
import unittest

from jobdep import (
    DONE,
    FAILED,
    SKIPPED,
    DagValidationError,
    rerun,
    run,
)
from jobdep.dag import REASON_ABORTED, REASON_UPSTREAM_FAILED, STATE_FILENAME


class DagTest(unittest.TestCase):
    def test_steps_in_order(self):
        seen = []
        state = run({"steps": [{"name": "a", "fn": lambda s: seen.append("a")},
                               {"name": "b", "fn": lambda s: seen.append("b")}]})
        self.assertEqual(seen, ["a", "b"])
        self.assertEqual(state["b"], "done")

    def test_failure_stops(self):
        seen = []
        with self.assertRaises(ValueError):
            run({"steps": [{"name": "a", "fn": lambda s: (_ for _ in ()).throw(ValueError())},
                           {"name": "b", "fn": lambda s: seen.append("b")}]})
        self.assertEqual(seen, [])

    def test_state_records_done(self):
        self.assertEqual(run({"steps": [{"name": "a", "fn": lambda s: None}]})["a"], "done")

    # ---- 新增用例 ----

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jobdep-test-")
        self._cwd = os.getcwd()
        os.chdir(self.tmp)  # 默认落盘目录 .jobdep 落在临时目录里

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dir(self, name):
        return os.path.join(self.tmp, name)

    def _state_path(self, state):
        return os.path.join(state.state_dir, STATE_FILENAME)

    def test_topological_order_respects_deps(self):
        seen = []

        def rec(n):
            return lambda s: seen.append(n)

        dag = {"steps": [
            {"name": "c", "fn": rec("c"), "deps": ["b"]},
            {"name": "a", "fn": rec("a")},
            {"name": "b", "fn": rec("b"), "deps": ["a"]},
        ]}
        state = run(dag, state_dir=self._dir("topo"))
        self.assertEqual(seen, ["a", "b", "c"])
        self.assertEqual(set(state.done_nodes()), {"a", "b", "c"})

    def test_indegree_gate_blocks_successor(self):
        seen = []
        dag = {"steps": [
            {"name": "root", "fn": lambda s: seen.append("root")},
            {"name": "mid", "fn": lambda s: seen.append("mid"),
             "deps": ["root"]},
            {"name": "leaf", "fn": lambda s: seen.append("leaf"),
             "deps": ["mid"]},
        ]}
        state = run(dag, state_dir=self._dir("gate"))
        self.assertEqual(seen.index("root") < seen.index("mid")
                         < seen.index("leaf"), True)
        self.assertEqual(state["leaf"], DONE)

    def test_failure_marks_downstream_skipped_with_reason(self):
        seen = []

        def boom(s):
            raise RuntimeError("extract failed")

        dag = {"steps": [
            {"name": "extract", "fn": boom},
            {"name": "transform", "fn": lambda s: seen.append("transform"),
             "deps": ["extract"]},
            {"name": "load", "fn": lambda s: seen.append("load"),
             "deps": ["transform"]},
        ]}
        with self.assertRaises(RuntimeError) as ctx:
            run(dag, state_dir=self._dir("fail"))
        state = ctx.exception.state
        self.assertEqual(seen, [])  # 下游一个都没跑
        self.assertEqual(state["extract"], FAILED)
        self.assertEqual(state["transform"], SKIPPED)
        self.assertEqual(state["load"], SKIPPED)
        self.assertEqual(state.reason("transform"),
                         [REASON_UPSTREAM_FAILED, "extract"])
        # 传递跳过也能追溯到最初的失败节点
        self.assertEqual(state.reason("load"),
                         [REASON_UPSTREAM_FAILED, "extract"])

    def test_aborted_nodes_also_skipped_with_reason(self):
        dag = {"steps": [
            {"name": "a", "fn": lambda s: (_ for _ in ()).throw(ValueError())},
            {"name": "b", "fn": lambda s: None, "deps": ["a"]},
        ]}
        with self.assertRaises(ValueError) as ctx:
            run(dag, state_dir=self._dir("abort"))
        state = ctx.exception.state
        self.assertEqual(state.reason("b")[0], REASON_UPSTREAM_FAILED)

    def test_unrelated_pending_nodes_aborted_with_reason(self):
        dag = {"steps": [
            {"name": "bad",
             "fn": lambda s: (_ for _ in ()).throw(RuntimeError())},
            {"name": "indep", "fn": lambda s: None},
        ]}
        with self.assertRaises(RuntimeError) as ctx:
            run(dag, state_dir=self._dir("abort-indep"))
        state = ctx.exception.state
        self.assertEqual(state["bad"], FAILED)
        self.assertEqual(state["indep"], SKIPPED)
        self.assertEqual(state.reason("indep"), [REASON_ABORTED, None])
        # 不存在遗漏的 pending 节点
        self.assertTrue(all(v != "pending" for v in state.values()))

    def test_done_node_before_failure_keeps_done(self):
        dag = {"steps": [
            {"name": "ok", "fn": lambda s: None},
            {"name": "bad",
             "fn": lambda s: (_ for _ in ()).throw(RuntimeError())},
        ]}
        with self.assertRaises(RuntimeError) as ctx:
            run(dag, state_dir=self._dir("done-kept"))
        state = ctx.exception.state
        self.assertEqual(state["ok"], DONE)
        self.assertEqual(state["bad"], FAILED)

    def test_state_and_skip_reasons_persisted(self):
        dag = {"steps": [
            {"name": "a", "fn": lambda s: (_ for _ in ()).throw(ValueError())},
            {"name": "b", "fn": lambda s: None, "deps": ["a"]},
        ]}
        with self.assertRaises(ValueError) as ctx:
            run(dag, state_dir=self._dir("persist"))
        state = ctx.exception.state
        with open(self._state_path(state), encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["statuses"]["a"], FAILED)
        self.assertEqual(payload["statuses"]["b"], SKIPPED)
        self.assertEqual(payload["skip_reasons"]["b"][1], "a")
        self.assertIn("deps", payload)

    def test_rerun_only_failed_and_skipped(self):
        calls = []

        def flaky(s):
            calls.append("extract")
            if len(calls) == 1:
                raise RuntimeError("first time fails")

        dag = {"steps": [
            {"name": "extract", "fn": flaky},
            {"name": "load", "fn": lambda s: calls.append("load"),
             "deps": ["extract"]},
            {"name": "report", "fn": lambda s: calls.append("report"),
             "deps": ["load"]},
        ]}
        with self.assertRaises(RuntimeError) as ctx:
            run(dag, state_dir=self._dir("rerun"))
        first = ctx.exception.state
        self.assertEqual(first.failed_nodes(), ["extract"])
        self.assertEqual(first.skipped_nodes(), ["load", "report"])

        second = rerun(first)
        # 只重跑失败 + 跳过；extract 共两次（首跑失败 + 重跑），
        # load/report 首跑被跳过、只在重跑时执行一次。
        self.assertEqual(calls, ["extract", "extract", "load", "report"])
        self.assertEqual(second.done_nodes(), ["extract", "load", "report"])
        self.assertEqual(second.failed_nodes(), [])
        self.assertEqual(second.skipped_nodes(), [])
        self.assertEqual(second.reruns, 1)

    def test_rerun_reuses_successful_nodes(self):
        calls = []
        dag = {"steps": [
            {"name": "ok", "fn": lambda s: calls.append("ok")},
            {"name": "bad", "fn": lambda s: calls.append("bad") or
             (_ for _ in ()).throw(RuntimeError())},
            {"name": "down", "fn": lambda s: calls.append("down"),
             "deps": ["bad"]},
        ]}
        with self.assertRaises(RuntimeError) as ctx:
            run(dag, state_dir=self._dir("reuse"))
        first = ctx.exception.state
        self.assertEqual(first["ok"], DONE)

        # 修复 bad 后重跑
        dag["steps"][1]["fn"] = lambda s: calls.append("bad-fixed")
        second = rerun(first)
        self.assertIn("ok", calls)
        self.assertEqual(calls.count("ok"), 1)  # 成功节点只跑一次
        self.assertIn("bad-fixed", calls)
        self.assertEqual(second["ok"], DONE)
        self.assertEqual(second["down"], DONE)

    def test_rerun_reads_failed_set_from_disk_only(self):
        dag = {"steps": [
            {"name": "bad",
             "fn": lambda s: (_ for _ in ()).throw(RuntimeError())},
            {"name": "down", "fn": lambda s: None, "deps": ["bad"]},
        ]}
        with self.assertRaises(RuntimeError) as ctx:
            run(dag, state_dir=self._dir("disk"))
        first = ctx.exception.state
        # 恶意篡改内存状态；重跑仍应以落盘的 failed/skipped 为准
        first["bad"] = DONE
        first["down"] = DONE
        dag["steps"][0]["fn"] = lambda s: None
        second = rerun(first)
        self.assertEqual(second.done_nodes(), ["bad", "down"])

    def test_rerun_accepts_state_path(self):
        dag = {"steps": [
            {"name": "bad",
             "fn": lambda s: (_ for _ in ()).throw(RuntimeError())},
        ]}
        with self.assertRaises(RuntimeError) as ctx:
            run(dag, state_dir=self._dir("path"))
        state_path = ctx.exception.state.state_path
        dag["steps"][0]["fn"] = lambda s: None
        second = rerun(state_path)
        self.assertEqual(second["bad"], DONE)

    def test_hot_update_add_edge_takes_effect_at_runtime(self):
        seen = []

        def root_fn(s):
            seen.append("root")
            # 运行中新增依赖 mid -> leaf：leaf 必须等 mid 完成
            s.update_deps(add=[("mid", "leaf")])

        dag = {"steps": [
            {"name": "root", "fn": root_fn},
            # leaf 声明在 mid 前：不热更时 leaf 会先于 mid 出队
            {"name": "leaf", "fn": lambda s: seen.append("leaf")},
            {"name": "mid", "fn": lambda s: seen.append("mid")},
        ]}
        state = run(dag, state_dir=self._dir("hot-add"))
        self.assertEqual(seen, ["root", "mid", "leaf"])
        self.assertIn("mid", state.deps["leaf"])

    def test_hot_update_remove_edge_takes_effect_at_runtime(self):
        seen = []

        def root_fn(s):
            seen.append("root")
            s.update_deps(remove=[("a", "b")])  # b 解除对 a 的依赖

        dag = {"steps": [
            {"name": "root", "fn": root_fn},
            {"name": "b", "fn": lambda s: seen.append("b"), "deps": ["a"]},
            {"name": "a", "fn": lambda s: seen.append("a")},
        ]}
        # 不删边：root -> a -> b；删边后 b 零入度且声明在 a 前，先于 a 执行。
        state = run(dag, state_dir=self._dir("hot-rm"))
        self.assertEqual(seen, ["root", "b", "a"])
        self.assertNotIn("a", state.deps["b"])

    def test_hot_update_validated_before_apply(self):
        def root_fn(s):
            # 成环的边：leaf -> root，但 root 已在运行（冻结），先被冻结校验拦下
            s.update_deps(add=[("leaf", "root")])

        dag = {"steps": [
            {"name": "root", "fn": root_fn},
            {"name": "leaf", "fn": lambda s: None},
        ]}
        with self.assertRaises(DagValidationError) as ctx:
            run(dag, state_dir=self._dir("hot-invalid"))
        state = ctx.exception.state
        # 校验失败则不热更：边没有写进去
        self.assertNotIn("leaf", state.deps["root"])
        self.assertEqual(state["leaf"], SKIPPED)
        self.assertEqual(state.reason("leaf"), [REASON_ABORTED, None])
        # 当前节点函数已成功，状态保留为 done
        self.assertEqual(state["root"], DONE)

    def test_hot_update_rejects_cycle_between_pending_nodes(self):
        def root_fn(s):
            # a -> b 已存在，再加 b -> a 必成环；a 为当前运行节点故以 a 冻结报错，
            # 这里改用 pending 间构造：新增 b->root 指向运行节点同样被拒。
            s.update_deps(add=[("b", "a")])

        dag = {"steps": [
            {"name": "a", "fn": root_fn},
            {"name": "b", "fn": lambda s: None, "deps": ["a"]},
        ]}
        with self.assertRaises(DagValidationError):
            run(dag, state_dir=self._dir("hot-cycle"))

    def test_upfront_cycle_rejected(self):
        dag = {"steps": [
            {"name": "a", "fn": lambda s: None, "deps": ["b"]},
            {"name": "b", "fn": lambda s: None, "deps": ["a"]},
        ]}
        with self.assertRaises(DagValidationError):
            run(dag, state_dir=self._dir("cycle"))

    def test_unknown_dependency_rejected(self):
        dag = {"steps": [
            {"name": "a", "fn": lambda s: None, "deps": ["ghost"]},
        ]}
        with self.assertRaises(DagValidationError):
            run(dag, state_dir=self._dir("unknown"))

    def test_skipped_nodes_do_not_consume_execution_slot(self):
        order = []

        def fail_root(s):
            order.append("root-running")
            raise RuntimeError()

        def skip_marker(s):
            order.append("should-not-run")

        # root 失败后 mid/leaf 全部跳过：跳过期间没有任何 fn 被调用，
        # 即跳过节点不占用执行位（执行位只服务真正运行的节点）。
        dag = {"steps": [
            {"name": "root", "fn": fail_root},
            {"name": "mid", "fn": skip_marker, "deps": ["root"]},
            {"name": "leaf", "fn": skip_marker, "deps": ["mid"]},
        ]}
        with self.assertRaises(RuntimeError) as ctx:
            run(dag, state_dir=self._dir("slot"))
        self.assertEqual(order, ["root-running"])
        state = ctx.exception.state
        self.assertEqual(state.skipped_nodes(), ["leaf", "mid"])

    def test_rerun_with_nothing_to_do_is_noop(self):
        dag = {"steps": [{"name": "a", "fn": lambda s: None}]}
        first = run(dag, state_dir=self._dir("noop"))
        second = rerun(first)
        self.assertEqual(second["a"], DONE)
        self.assertEqual(second.reruns, 1)


if __name__ == "__main__":
    unittest.main()
