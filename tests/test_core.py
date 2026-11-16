import json
import os
import shutil
import tempfile
import unittest

from reproenv import build, cleanup, compare, configure, find_envs, rerun_step, reset, verify

REPORT = {
    "id": "r-1",
    "version": "1.4.2",
    "deps": ["requests==2.31.0", "flask==2.0.1"],
    "data": {"seed": 1},
    "steps": [
        {"name": "启动服务", "expect": "listening"},
        {"name": "发送请求", "expect": "200"},
    ],
}


class ReproEnvTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.tmp = tempfile.mkdtemp(prefix="reproenv-test-")
        configure(root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- build：记录版本、依赖、初始数据与环境标识 ----

    def test_build_records_version_deps_and_data(self):
        env = build(REPORT)
        self.assertEqual(env["version"], "1.4.2")
        self.assertEqual(env["deps"], ["requests==2.31.0", "flask==2.0.1"])
        self.assertEqual(env["data"], {"seed": 1})
        self.assertEqual(env["state"], "ready")
        self.assertTrue(env["env_id"].startswith("env-"))
        self.assertEqual(env["cleanup"], "pending")

    def test_env_id_written_into_report_manifest(self):
        env = build(REPORT)
        with open(os.path.join(env["workspace"], "manifest.json"),
                  encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertEqual(manifest["env_id"], env["env_id"])
        self.assertEqual(manifest["report"]["env_id"], env["env_id"])
        self.assertEqual(manifest["report"]["id"], "r-1")
        # 原始报告不被就地改写。
        self.assertNotIn("env_id", REPORT)

    def test_initial_data_persisted(self):
        env = build(REPORT)
        with open(os.path.join(env["workspace"], "data.json"),
                  encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"seed": 1})

    # ---- 环境隔离 ----

    def test_envs_have_distinct_workspaces(self):
        first = build(REPORT)
        second = build(REPORT)
        self.assertNotEqual(first["env_id"], second["env_id"])
        self.assertNotEqual(first["workspace"], second["workspace"])
        self.assertTrue(os.path.isdir(first["workspace"]))
        self.assertTrue(os.path.isdir(second["workspace"]))
        # 工作区互不影响：一个写文件，另一个看不到。
        with open(os.path.join(first["workspace"], "marker.txt"), "w") as fh:
            fh.write("x")
        self.assertFalse(
            os.path.exists(os.path.join(second["workspace"], "marker.txt")))

    def test_workspaces_under_configured_root(self):
        env = build(REPORT)
        self.assertTrue(env["workspace"].startswith(self.tmp))

    # ---- verify：步骤结果、失败定位、重复一致 ----

    def test_verify_all_steps_pass(self):
        env = build(REPORT)
        out = verify(env)
        self.assertTrue(out["ok"])
        self.assertIsNone(out["failed_step"])
        self.assertEqual([r["step"] for r in out["results"]],
                         ["启动服务", "发送请求"])
        self.assertEqual(out["results"][0]["expect"], "listening")
        self.assertEqual(out["results"][0]["actual"], "listening")
        self.assertTrue(all(r["ok"] for r in out["results"]))
        self.assertEqual(out["version"], "1.4.2")
        self.assertEqual(out["env_id"], env["env_id"])

    def test_verify_reports_which_step_failed_and_stops(self):
        report = dict(REPORT, steps=[
            {"name": "启动服务", "expect": "listening", "actual": "listening"},
            {"name": "发送请求", "expect": "200", "actual": "500"},
            {"name": "收尾", "expect": "done"},
        ])
        env = build(report)
        out = verify(env)
        self.assertFalse(out["ok"])
        self.assertEqual(out["failed_step"], "发送请求")
        # 失败即停，后续步骤不执行。
        self.assertEqual([r["step"] for r in out["results"]],
                         ["启动服务", "发送请求"])

    def test_fail_steps_marker(self):
        report = dict(REPORT, fail_steps=["发送请求"])
        env = build(report)
        out = verify(env)
        self.assertFalse(out["ok"])
        self.assertEqual(out["failed_step"], "发送请求")
        self.assertEqual(out["results"][1]["actual"], "")

    def test_repeated_verify_is_consistent(self):
        first = build(REPORT)
        second = build(REPORT)
        out1 = verify(first)
        out2 = verify(second)
        cmp = compare(out1, out2)
        self.assertTrue(cmp["consistent"])
        self.assertEqual(cmp["env_ids"], [first["env_id"], second["env_id"]])
        self.assertTrue(all(row["ok_all"] for row in cmp["steps"]))

    def test_compare_detects_divergence(self):
        env_good = build(REPORT)
        report_bad = dict(REPORT, fail_steps=["发送请求"])
        env_bad = build(report_bad)
        cmp = compare(verify(env_good), verify(env_bad))
        self.assertFalse(cmp["consistent"])
        self.assertFalse(cmp["steps"][1]["ok_all"])

    def test_verify_result_files_persisted_per_step(self):
        env = build(REPORT)
        verify(env)
        results_dir = os.path.join(env["workspace"], "results")
        self.assertEqual(sorted(os.listdir(results_dir)),
                         ["0-启动服务.json", "1-发送请求.json"])
        with open(os.path.join(results_dir, "1-发送请求.json"),
                  encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["expect"], "200")

    # ---- 单独重跑 ----

    def test_rerun_single_step(self):
        env = build(REPORT)
        full = verify(env)
        rerun = rerun_step(env, "发送请求")
        self.assertEqual(rerun["step"], "发送请求")
        self.assertTrue(rerun["ok"])
        # 单独重跑：只执行指定步骤，输出里其余步骤沿用上次结果。
        merged = verify(env, only=["发送请求"])
        self.assertEqual([r["step"] for r in merged["results"]],
                         ["启动服务", "发送请求"])
        self.assertTrue(merged["ok"])
        self.assertEqual(len(full["results"]), 2)

    def test_rerun_unknown_step_raises(self):
        env = build(REPORT)
        with self.assertRaises(ValueError):
            verify(env, only=["不存在"])

    # ---- 依赖拉取失败：停在准备阶段 ----

    def test_dependency_failure_blocks_verify(self):
        report = dict(REPORT, fail_deps=["flask==2.0.1"])
        env = build(report)
        self.assertEqual(env["state"], "prepare_failed")
        self.assertEqual(env["prepare_error"]["name"], "flask")
        self.assertEqual(env["prepare_error"]["version"], "2.0.1")
        self.assertEqual(env["prepare_error"]["dep"], "flask==2.0.1")
        with self.assertRaises(RuntimeError):
            verify(env)
        # 准备失败也有独立工作目录，日志保留失败包名和版本。
        log_path = os.path.join(env["workspace"], "logs", "prepare.log")
        with open(log_path, encoding="utf-8") as fh:
            log = fh.read()
        self.assertIn("flask", log)
        self.assertIn("2.0.1", log)
        self.assertIn("FAIL", log)
        # 失败前已成功的依赖也有记录。
        self.assertIn("requests==2.31.0", log)

    def test_dependency_failure_by_bare_name(self):
        report = dict(REPORT, fail_deps=["flask"])
        env = build(report)
        self.assertEqual(env["state"], "prepare_failed")
        self.assertEqual(env["prepare_error"]["name"], "flask")

    def test_injected_installer_failure_stops_prepare(self):
        def bad_installer(record, dep):
            raise RuntimeError("network unreachable")

        env = build(REPORT, installer=bad_installer)
        self.assertEqual(env["state"], "prepare_failed")
        self.assertIn("network unreachable",
                      env["prepare_error"]["error"])

    def test_dict_style_deps(self):
        report = dict(REPORT, deps=[{"name": "requests", "version": "2.31.0"}])
        env = build(report)
        self.assertEqual(env["deps"], ["requests==2.31.0"])
        self.assertEqual(env["state"], "ready")

    # ---- 资源超预期提前终止 ----

    def test_max_steps_limit_aborts(self):
        report = dict(REPORT, resource_limit={"max_steps": 1})
        env = build(report)
        out = verify(env)
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "resource_limit:max_steps")
        self.assertEqual(out["aborted_at"], "启动服务")
        self.assertEqual(out["results"], [])

    def test_workspace_size_limit_aborts(self):
        report = dict(REPORT, resource_limit={"max_workspace_bytes": 1})
        env = build(report)
        out = verify(env)
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"],
                         "resource_limit:max_workspace_bytes")

    def test_step_timeout_limit_aborts(self):
        def slow_runner(record, step):
            time_sleep = __import__("time").sleep
            time_sleep(0.05)
            return step.get("expect", "")

        report = dict(REPORT, resource_limit={"step_timeout_seconds": 0.01})
        env = build(report)
        out = verify(env, runner=slow_runner)
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"],
                         "resource_limit:step_timeout_seconds")
        self.assertEqual(out["aborted_at"], "启动服务")

    # ---- 清理 ----

    def test_cleanup_removes_workspace(self):
        env = build(REPORT)
        workspace = env["workspace"]
        out = cleanup(env)
        self.assertTrue(out["removed"])
        self.assertEqual(out["cleanup"], "removed")
        self.assertFalse(os.path.exists(workspace))

    def test_cleanup_is_idempotent_and_blocks_verify(self):
        env = build(REPORT)
        cleanup(env)
        again = cleanup(env)
        self.assertTrue(again["removed"])
        with self.assertRaises(RuntimeError):
            verify(env)

    def test_cleanup_one_env_does_not_touch_others(self):
        first = build(REPORT)
        second = build(REPORT)
        cleanup(first)
        self.assertFalse(os.path.exists(first["workspace"]))
        self.assertTrue(os.path.isdir(second["workspace"]))
        self.assertTrue(verify(second)["ok"])

    # ---- 入参与查询 ----

    def test_reject_non_dict_report(self):
        with self.assertRaises(ValueError):
            build(["r-1"])

    def test_reject_bad_dep_spec(self):
        with self.assertRaises(ValueError):
            build(dict(REPORT, deps=[""]))

    def test_unknown_env_raises(self):
        with self.assertRaises(ValueError):
            verify("env-999")

    def test_find_envs_by_report_id(self):
        build(REPORT)
        build(REPORT)
        build(dict(REPORT, id="r-2"))
        self.assertEqual(len(find_envs("r-1")), 2)
        self.assertEqual(len(find_envs()), 3)


if __name__ == "__main__":
    unittest.main()
