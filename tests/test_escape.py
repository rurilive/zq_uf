import unittest

from containerexit import check, mark_false_positive, report, reset


class CheckTest(unittest.TestCase):
    def setUp(self):
        reset()

    # ---- 核心原则：单项不构成高危 -------------------------------------

    def test_privileged_alone_is_medium_not_high(self):
        got = check({"name": "c1", "host": "h1", "privileged": True})
        self.assertEqual(got["level"], "medium")
        self.assertTrue(got["hit"])
        self.assertTrue(all(c["level"] != "high" for c in got["combos"]))

    def test_socket_alone_is_medium(self):
        got = check({
            "name": "c2", "host": "h1",
            "mounts": ["/var/run/docker.sock"],
        })
        self.assertEqual(got["level"], "medium")
        self.assertEqual(got["combos"][0]["id"], "M01")

    def test_sys_admin_alone_is_medium(self):
        got = check({"name": "c3", "host": "h1", "caps": ["SYS_ADMIN"]})
        self.assertEqual(got["level"], "medium")

    def test_clean_container_is_none(self):
        got = check({"name": "c4", "host": "h1"})
        self.assertEqual(got["level"], "none")
        self.assertFalse(got["hit"])
        self.assertEqual(got["combos"], [])
        self.assertEqual(got["advice"]["offline"], [])
        self.assertTrue(got["advice"]["none"])

    # ---- 组合命中才高危 -----------------------------------------------

    def test_privileged_with_host_pid_is_high(self):
        got = check({
            "name": "c5", "host": "h1",
            "privileged": True, "host_pid": True,
        })
        self.assertEqual(got["level"], "high")
        self.assertIn("H01", [c["id"] for c in got["combos"]])
        # 命中依据必须带实际取值
        basis_by_key = {b["key"]: b["value"] for b in got["combos"][0]["basis"]}
        self.assertEqual(basis_by_key["privileged"], True)
        self.assertEqual(basis_by_key["host_pid"], True)

    def test_privileged_with_writable_host_mount_is_high(self):
        got = check({
            "name": "c6", "host": "h1",
            "privileged": True,
            "mounts": [{"source": "/etc", "target": "/host/etc", "mode": "rw"}],
        })
        self.assertEqual(got["level"], "high")
        self.assertIn("H02", [c["id"] for c in got["combos"]])

    def test_non_privileged_sys_admin_plus_host_mount_is_high(self):
        got = check({
            "name": "c7", "host": "h1",
            "caps": ["CAP_SYS_ADMIN"],
            "mounts": ["/:/host"],
        })
        self.assertEqual(got["level"], "high")
        self.assertIn("H03", [c["id"] for c in got["combos"]])

    def test_socket_with_dangerous_caps_is_high(self):
        got = check({
            "name": "c8", "host": "h1",
            "caps": ["SYS_ADMIN"],
            "mounts": ["/var/run/docker.sock:/var/run/docker.sock"],
        })
        self.assertEqual(got["level"], "high")
        self.assertIn("H06", [c["id"] for c in got["combos"]])

    def test_host_pid_ptrace_is_high(self):
        got = check({
            "name": "c9", "host": "h1",
            "host_pid": True, "caps": ["SYS_PTRACE"],
        })
        self.assertEqual(got["level"], "high")
        self.assertIn("H07", [c["id"] for c in got["combos"]])

    def test_core_pattern_pipe_with_sys_admin_is_high(self):
        got = check({
            "name": "c10", "host": "h1",
            "caps": ["SYS_ADMIN"],
            "sysctls": {"kernel.core_pattern": "|/tmp/x"},
        })
        self.assertEqual(got["level"], "high")
        self.assertIn("H11", [c["id"] for c in got["combos"]])

    def test_readonly_mount_does_not_trigger_writable_rule(self):
        got = check({
            "name": "c11", "host": "h1",
            "privileged": True,
            "mounts": ["/etc:/host/etc:ro"],
        })
        # 只读挂载不满足 H02；特权单项仍是 medium
        self.assertEqual(got["level"], "medium")

    def test_block_device_with_privileged_is_high(self):
        got = check({
            "name": "c12", "host": "h1",
            "privileged": True,
            "mounts": ["/dev/sda:/dev/x"],
        })
        self.assertEqual(got["level"], "high")
        self.assertIn("H04", [c["id"] for c in got["combos"]])

    def test_cgroup_with_sys_admin_is_high(self):
        got = check({
            "name": "c13", "host": "h1",
            "caps": ["SYS_ADMIN"],
            "mounts": ["/sys/fs/cgroup:/sys/fs/cgroup"],
        })
        self.assertEqual(got["level"], "high")
        self.assertIn("H09", [c["id"] for c in got["combos"]])

    # ---- 依据与建议 ----------------------------------------------------

    def test_basis_covers_all_config_with_actual_values(self):
        got = check({
            "name": "c14", "host": "h1",
            "privileged": False,
            "caps": ["NET_ADMIN"],
            "mounts": ["/data:/data"],
            "host_net": True,
            "sysctls": {"net.ipv4.ip_forward": "1"},
            "seccomp": "unconfined",
        })
        keys = {b["key"] for b in got["basis"]}
        self.assertEqual(
            keys,
            {"privileged", "caps", "mounts", "host_pid", "host_ipc",
             "host_net", "host_uts", "sysctls", "seccomp", "apparmor"},
        )
        basis = {b["key"]: b["value"] for b in got["basis"]}
        self.assertEqual(basis["seccomp"], "unconfined")
        self.assertEqual(basis["caps"], ["NET_ADMIN"])

    def test_advice_split_by_downtime(self):
        got = check({
            "name": "c15", "host": "h1",
            "privileged": True, "host_pid": True,
        })
        self.assertTrue(got["advice"]["online"])
        self.assertTrue(got["advice"]["offline"])

    # ---- 跨宿主一致性 --------------------------------------------------

    def test_same_config_same_conclusion_across_hosts(self):
        cfg = {
            "privileged": True,
            "mounts": ["/etc:/host/etc"],
            "caps": ["NET_RAW"],
        }
        r1 = check(dict(cfg, name="a", host="host-a", image="img:1"))
        r2 = check(dict(cfg, name="b", host="host-b", image="img:1"))
        self.assertEqual(r1["config_hash"], r2["config_hash"])
        self.assertEqual(r1["level"], r2["level"])
        self.assertEqual(
            [c["id"] for c in r1["combos"]],
            [c["id"] for c in r2["combos"]],
        )

    def test_different_mount_changes_conclusion(self):
        r1 = check({"name": "a", "host": "h", "privileged": True})
        r2 = check({
            "name": "b", "host": "h", "privileged": True,
            "mounts": ["/etc:/e"],
        })
        self.assertNotEqual(r1["config_hash"], r2["config_hash"])
        self.assertEqual((r1["level"], r2["level"]), ("medium", "high"))

    # ---- 输入校验 ------------------------------------------------------

    def test_name_required(self):
        with self.assertRaises(ValueError):
            check({"host": "h1"})

    def test_bad_mount_raises(self):
        with self.assertRaises(ValueError):
            check({"name": "x", "host": "h", "mounts": [123]})


class ReportTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_host_groups_merge_containers(self):
        check({"name": "web", "host": "h1", "privileged": True, "host_pid": True})
        check({"name": "db", "host": "h1"})
        check({"name": "app", "host": "h2", "caps": ["SYS_ADMIN"]})
        got = report()

        self.assertEqual(got["containers"], 3)
        self.assertEqual(got["hosts"], 2)
        self.assertEqual(got["high"], 1)
        hosts = {g["host"]: g for g in got["host_groups"]}
        self.assertEqual(hosts["h1"]["container_count"], 2)
        self.assertEqual(hosts["h1"]["highest_level"], "high")
        # 高风险主机排在前面
        self.assertEqual(got["host_groups"][0]["host"], "h1")
        # 单容器结论可展开
        self.assertEqual(
            {c["container"] for c in hosts["h1"]["containers"]},
            {"web", "db"},
        )

    def test_config_groups_compare_hosts(self):
        cfg = {"privileged": True, "mounts": ["/etc:/e"]}
        check(dict(cfg, name="a", host="h1", image="img:1"))
        check(dict(cfg, name="b", host="h2", image="img:1"))
        got = report()

        same_hash = [g for g in got["config_groups"] if g["host_count"] == 2]
        self.assertEqual(len(same_hash), 1)
        group = same_hash[0]
        self.assertTrue(group["consistent"])
        self.assertEqual(group["level"], "high")
        self.assertEqual(len(group["basis"]), 10)

    def test_false_positive_marking(self):
        check({"name": "web", "host": "h1", "privileged": True, "host_pid": True})
        mark = mark_false_positive(
            "h1", "web",
            reason="宿主已通过外部策略阻断 ptrace 与命名空间共享",
            actual_config={"note": "实际运行时 hostPID 被准入控制器改写为 false"},
        )
        self.assertEqual(mark["level"], "high")
        self.assertIn("actual_config", mark)

        got = report()
        self.assertEqual(got["false_positives"], 1)
        # 标记后不计入活跃高危统计，但结论本身保留可复核
        self.assertEqual(got["high"], 0)
        self.assertEqual(got["marked"][0]["reason"], mark["reason"])
        host_group = next(g for g in got["host_groups"] if g["host"] == "h1")
        self.assertEqual(host_group["false_positives"], 1)
        container = next(c for c in host_group["containers"] if c["container"] == "web")
        self.assertTrue(container["false_positive"])

    def test_mark_unknown_container_raises(self):
        with self.assertRaises(ValueError):
            mark_false_positive("h1", "ghost")

    def test_mark_keeps_basis_snapshot_by_default(self):
        check({"name": "web", "host": "h1", "caps": ["SYS_ADMIN"],
               "mounts": ["/:/host"]})
        mark = mark_false_positive("h1", "web", reason="误报复核")
        keys = {b["key"] for b in mark["actual_config"]}
        self.assertIn("mounts", keys)
        self.assertIn("caps", keys)


if __name__ == "__main__":
    unittest.main()
