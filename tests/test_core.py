import json
import os
import shutil
import tempfile
import unittest

from reproenv import build, cleanup, compare, history, reset, verify
from reproenv import DepResolveError, ReportError, StepError

REPORT = {
    "id": "r-1",
    "version": "1.4.2",
    "deps": ["requests==2.31.0", "flask==2.0.1"],
    "initial_data": {"seed.txt": "hello", "config/conf.json": {"a": 1}},
    "steps": [
        {"name": "启动服务", "expect": "listening"},
        {"name": "发送请求", "expect": "200"},
    ],
}


class ReproEnvTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="reproenv-test-")
        reset(remove_dirs=True)

    def tearDown(self):
        reset(remove_dirs=True)
        shutil.rmtree(self.base, ignore_errors=True)

    # ---- build：记录版本、依赖、初始数据 ----

    def test_build_records_version_deps_and_data(self):
        env = build(REPORT, base_dir=self.base)
        self.assertEqual(env["version"], "1.4.2")
        self.assertEqual(env["deps"], REPORT["deps"])
        self.assertEqual(env["state"], "ready")
        self.assertEqual(env["cleanup"], "pending")
        # 解析后的包记录带包名、版本、体积
        names = [pkg["name"] for pkg in env["packages"]]
        self.assertEqual(names, ["requests", "flask"])
        self.assertTrue(all(pkg["status"] == "resolved" for pkg in env["packages"]))
        # 初始数据深拷贝并落盘
        self.assertEqual(env["initial_data"]["seed.txt"], "hello")
        self.assertTrue(os.path.exists(os.path.join(env["workspace"], "data", "seed.txt")))
        self.assertTrue(
            os.path.exists(os.path.join(env["workspace"], "data", "config", "conf.json"))
        )

    def test_env_id_written_into_report_snapshot(self):
        env = build(REPORT, base_dir=self.base)
        with open(os.path.join(env["workspace"], "report.json"), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["env_id"], env["env_id"])
        # 调用方传入的原报告不被修改
        self.assertNotIn("env_id", REPORT)

    # ---- 环境隔离 ----

    def test_envs_have_independent_workspaces(self):
        first = build(REPORT, base_dir=self.base)
        second = build(REPORT, base_dir=self.base)
        self.assertNotEqual(first["env_id"], second["env_id"])
        self.assertNotEqual(first["workspace"], second["workspace"])
        self.assertTrue(os.path.isdir(first["workspace"]))
        self.assertTrue(os.path.isdir(second["workspace"]))

        # 一个环境写文件，另一个看不到
        marker = os.path.join(first["workspace"], "data", "marker.txt")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("x")
        self.assertFalse(os.path.exists(os.path.join(second["workspace"], "data", "marker.txt")))

    # ---- verify：步骤结果与失败定位 ----

    def test_verify_success_path(self):
        env = build(REPORT, base_dir=self.base)
        out = verify(env)
        self.assertTrue(out["ok"])
        self.assertIsNone(out["failed_step"])
        self.assertEqual(len(out["results"]), 2)
        self.assertEqual(out["results"][0]["step"], "启动服务")
        self.assertEqual(out["env_id"], env["env_id"])
        self.assertEqual(out["version"], "1.4.2")
        self.assertIn("cleanup", out)

    def test_verify_failure_reports_stuck_step_and_stops(self):
        report = {
            "id": "r-fail",
            "version": "1.0",
            "deps": ["requests==2.31.0"],
            "steps": [
                {"name": "启动服务", "expect": "listening"},
                {"name": "发送请求", "expect": "200", "actual": "500"},
                {"name": "不应执行", "expect": "x"},
            ],
        }
        env = build(report, base_dir=self.base)
        out = verify(env)
        self.assertFalse(out["ok"])
        self.assertEqual(out["failed_step"], "发送请求")
        self.assertEqual(len(out["results"]), 2)  # 失败即停，第三步没跑
        self.assertTrue(out["results"][0]["ok"])
        self.assertFalse(out["results"][1]["ok"])
        self.assertIn("200", out["results"][1]["detail"])
        self.assertEqual(env and _state_of(env), "verify_failed")

    def test_callable_step_and_custom_runner(self):
        report = {
            "id": "r-run",
            "version": "2.0",
            "deps": [],
            "steps": [
                {"name": "脚本步骤", "expect": 7, "run": lambda step, env: 7},
                {"name": "炸了", "run": lambda step, env: (_ for _ in ()).throw(RuntimeError("boom"))},
            ],
        }
        env = build(report, base_dir=self.base)
        out = verify(env)
        self.assertTrue(out["results"][0]["ok"])
        self.assertEqual(out["results"][0]["actual"], 7)
        self.assertFalse(out["results"][1]["ok"])
        self.assertIn("boom", out["results"][1]["detail"])
        self.assertEqual(out["failed_step"], "炸了")

        def always_fail(step, env):
            raise StepError("runner 拒绝")

        out2 = verify(env, runner=always_fail)
        self.assertFalse(out2["ok"])
        self.assertIn("runner 拒绝", out2["results"][0]["detail"])

    def test_dict_style_runner_and_abort(self):
        env = build(
            {"id": "r-dict", "version": "1", "deps": [],
             "steps": [{"name": "s1"}, {"name": "s2"}]},
            base_dir=self.base,
        )

        def dict_runner(step, env):
            if step["name"] == "s2":
                return {"ok": False, "actual": "x", "detail": "字典风格失败", "abort": True,
                        "reason": "aborted-by-runner"}
            return {"ok": True, "actual": "y"}

        out = verify(env, runner=dict_runner)
        self.assertFalse(out["ok"])
        self.assertEqual(out["failed_step"], "s2")
        self.assertTrue(out["results"][-1]["aborted"])
        self.assertEqual(out["reason"], "aborted-by-runner")
        self.assertEqual(env["state"], "aborted")

    # ---- 依赖拉取失败：停在准备阶段，日志保留包名和版本 ----

    def test_dependency_failure_stops_preparation(self):
        report = dict(REPORT, deps=["requests==2.31.0", "missing-lib==9.9.9"])
        env = build(report, base_dir=self.base)
        self.assertEqual(env["state"], "prepare_failed")
        # 失败包名和版本进日志
        joined = "\n".join(env["logs"])
        self.assertIn("missing-lib", joined)
        self.assertIn("9.9.9", joined)
        self.assertIn("依赖拉取失败", joined)
        # 磁盘日志同样保留
        log_path = os.path.join(env["workspace"], "logs", "build.log")
        with open(log_path, encoding="utf-8") as fh:
            self.assertIn("missing-lib==9.9.9", fh.read())
        # 失败包之后没有继续装
        self.assertEqual([p["name"] for p in env["packages"]], ["requests"])

    def test_verify_never_runs_steps_after_prepare_failure(self):
        report = dict(REPORT, deps=["fail-pkg==1.0"])
        env = build(report, base_dir=self.base)
        out = verify(env)
        self.assertFalse(out["ok"])
        self.assertEqual(out["results"], [])
        self.assertIsNone(out["failed_step"])
        self.assertEqual(out["status"], "prepare_failed")

    def test_custom_resolver_failure_contract(self):
        def resolver(spec, report):
            name, version = spec.split("==")
            raise DepResolveError(name, version, "私有源 404", spec)

        env = build(dict(REPORT, deps=["pkg==1.2.3"]), base_dir=self.base, resolver=resolver)
        self.assertEqual(env["state"], "prepare_failed")
        self.assertIn("pkg==1.2.3", "\n".join(env["logs"]))
        self.assertIn("私有源 404", "\n".join(env["logs"]))

    def test_offline_report_fails_all_deps(self):
        env = build(dict(REPORT, offline=True), base_dir=self.base)
        self.assertEqual(env["state"], "prepare_failed")
        self.assertIn("requests==2.31.0", "\n".join(env["logs"]))

    def test_bad_report_raises_value_error(self):
        with self.assertRaises(ValueError):
            build(["not", "dict"], base_dir=self.base)
        with self.assertRaises(ValueError):
            build(dict(REPORT, deps="requests==1.0"), base_dir=self.base)
        with self.assertRaises(ValueError):
            build(dict(REPORT, deps=["requests>1.0"]), base_dir=self.base)

    # ---- 资源超预期提前终止 ----

    def test_disk_limit_aborts_during_preparation(self):
        env = build(REPORT, base_dir=self.base, limits={"disk_bytes": 100_000})
        self.assertEqual(env["state"], "aborted")
        self.assertEqual(env["reason"], "resource-limit:disk_bytes")
        self.assertIn("提前终止", "\n".join(env["logs"]))
        out = verify(env)
        self.assertFalse(out["ok"])
        self.assertEqual(out["results"], [])

    def test_step_count_limit_aborts_before_running(self):
        report = dict(REPORT, limits={"max_steps": 1})
        env = build(report, base_dir=self.base)
        out = verify(env)
        self.assertFalse(out["ok"])
        self.assertEqual(env["state"], "aborted")
        self.assertEqual(out["results"], [])

    def test_timeout_limit_aborts_mid_verification(self):
        report = {
            "id": "r-time",
            "version": "1",
            "deps": [],
            "limits": {"timeout_seconds": 5},
            "steps": [
                {"name": "s1", "expect": "a", "duration": 3},
                {"name": "s2", "expect": "b", "duration": 3},
            ],
        }
        env = build(report, base_dir=self.base)
        out = verify(env)
        self.assertFalse(out["ok"])
        self.assertEqual(env["state"], "aborted")
        self.assertTrue(out["results"][-1]["aborted"])
        self.assertEqual(out["failed_step"], "s2")

    # ---- 验证步骤单独重跑 ----

    def test_single_step_rerun(self):
        report = {
            "id": "r-rerun",
            "version": "1",
            "deps": [],
            "steps": [
                {"name": "s1", "expect": "a"},
                {"name": "s2", "expect": "b", "actual": "wrong"},
            ],
        }
        env = build(report, base_dir=self.base)
        first = verify(env)
        self.assertFalse(first["ok"])
        self.assertEqual(first["failed_step"], "s2")

        # 修正步骤后单独重跑（通过下标和名称都可以）
        env["steps"][1]["actual"] = "b"
        rerun = verify(env, step=1)
        self.assertEqual(rerun["rerun"], "s2")
        self.assertEqual(len(rerun["results"]), 1)
        self.assertTrue(rerun["results"][0]["ok"])
        rerun_by_name = verify(env, step="s2")
        self.assertTrue(rerun_by_name["results"][0]["ok"])

        # 整环境状态在单步重跑时不被覆盖，完整重跑才聚合
        self.assertEqual(_state_of(env), "verify_failed")
        full = verify(env)
        self.assertTrue(full["ok"])
        self.assertEqual(_state_of(env), "verified")

    # ---- 同报告多次复现可比对，且结果一致 ----

    def test_repeat_verification_is_consistent_and_comparable(self):
        e1 = build(REPORT, base_dir=self.base)
        e2 = build(REPORT, base_dir=self.base)
        o1 = verify(e1)
        o2 = verify(e2)
        self.assertTrue(o1["ok"] and o2["ok"])

        result = compare(e1, e2)
        self.assertTrue(result["consistent"])
        self.assertEqual(result["report"], "r-1")
        self.assertEqual(
            [c["canonical"] for c in result["runs"]][0],
            [c["canonical"] for c in result["runs"]][1],
        )

        rows = history("r-1")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["ok"] for row in rows))

    def test_compare_detects_divergence(self):
        e1 = build(
            {"id": "r-x", "version": "1", "deps": [], "steps": [{"name": "s", "expect": "200"}]},
            base_dir=self.base,
        )
        e2 = build(
            {"id": "r-x", "version": "1", "deps": [],
             "steps": [{"name": "s", "expect": "200", "actual": "500"}]},
            base_dir=self.base,
        )
        verify(e1)
        verify(e2)
        result = compare(e1, e2)
        self.assertFalse(result["consistent"])
        self.assertTrue(result["diffs"])

    def test_compare_rejects_different_reports(self):
        e1 = build(dict(REPORT, id="r-a"), base_dir=self.base)
        e2 = build(dict(REPORT, id="r-b"), base_dir=self.base)
        verify(e1)
        verify(e2)
        with self.assertRaises(ValueError):
            compare(e1, e2)

    # ---- 用完清理干净 ----

    def test_cleanup_removes_workspace_and_is_idempotent(self):
        env = build(REPORT, base_dir=self.base)
        workspace = env["workspace"]
        self.assertTrue(os.path.isdir(workspace))
        out = cleanup(env)
        self.assertTrue(out["removed"])
        self.assertEqual(out["cleanup"], "removed")
        self.assertFalse(os.path.exists(workspace))
        # 幂等：再清一次不报错
        again = cleanup(env)
        self.assertTrue(again["removed"])
        # 清理后拒绝再验证
        with self.assertRaises(ValueError):
            verify(env)

    def test_cleanup_one_env_does_not_touch_others(self):
        e1 = build(REPORT, base_dir=self.base)
        e2 = build(REPORT, base_dir=self.base)
        cleanup(e1)
        self.assertFalse(os.path.exists(e1["workspace"]))
        self.assertTrue(os.path.isdir(e2["workspace"]))

    def test_unknown_env_raises(self):
        with self.assertRaises(ValueError):
            verify("env-999")
        with self.assertRaises(ValueError):
            cleanup({"env_id": "env-999"})

    def test_limit_aliases_and_empty_report(self):
        env = build(
            {"id": "r-empty", "version": "0.1", "deps": [], "steps": []},
            base_dir=self.base,
            limits={"disk": 10, "steps": 3, "timeout": 9},
        )
        self.assertEqual(
            env["limits"],
            {"disk_bytes": 10, "max_steps": 3, "timeout_seconds": 9},
        )
        out = verify(env)
        self.assertTrue(out["ok"])
        self.assertEqual(out["results"], [])

    def test_data_alias_and_failed_env_keeps_workspace_logs(self):
        env = build(
            {"id": "r-data", "version": "1", "deps": ["missing-x==1.0"],
             "data": {"f.txt": "v"}},
            base_dir=self.base,
        )
        self.assertEqual(env["state"], "prepare_failed")
        self.assertEqual(env["initial_data"], {"f.txt": "v"})
        # 失败环境保留 workspace 供排查，cleanup 才删除
        self.assertTrue(os.path.isfile(os.path.join(env["workspace"], "data", "f.txt")))
        self.assertTrue(os.path.isfile(os.path.join(env["workspace"], "logs", "build.log")))
        cleanup(env)
        self.assertFalse(os.path.exists(env["workspace"]))

    def test_rerun_unknown_step_raises(self):
        env = build(REPORT, base_dir=self.base)
        with self.assertRaises(ValueError):
            verify(env, step="不存在的步骤")
        with self.assertRaises(ValueError):
            verify(env, step=99)


def _state_of(env):
    from reproenv.core import _ENVS

    return _ENVS[env["env_id"]]["state"]


if __name__ == "__main__":
    unittest.main()
