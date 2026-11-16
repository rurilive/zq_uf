import unittest

from reproenv import build, cleanup, verify

REPORT = {
    "id": "r-1",
    "version": "1.4.2",
    "deps": ["requests==2.31.0", "flask==2.0.1"],
    "steps": [
        {"name": "启动服务", "expect": "listening"},
        {"name": "发送请求", "expect": "200"},
    ],
}


class BuildTest(unittest.TestCase):
    def test_build_records_version_and_deps(self):
        env = build(REPORT)
        self.assertEqual(env["version"], "1.4.2")
        self.assertEqual(env["deps"], ["requests==2.31.0", "flask==2.0.1"])
        self.assertEqual(env["state"], "ready")

    def test_envs_share_same_workspace(self):
        first = build(REPORT)
        second = build(REPORT)
        self.assertNotEqual(first["env_id"], second["env_id"])
        self.assertEqual(first["workspace"], second["workspace"])

    def test_verify_reports_ok_without_checking_expect(self):
        env = build(REPORT)
        out = verify(env)
        self.assertTrue(out["ok"])
        self.assertIsNone(out["failed_step"])
        self.assertEqual(out["results"][1], {"step": "发送请求", "ok": True})

    def test_cleanup_keeps_workspace(self):
        env = build(REPORT)
        out = cleanup(env)
        self.assertFalse(out["removed"])
        self.assertEqual(out["kept"], "/work")

    def test_reject_non_dict_report(self):
        with self.assertRaises(ValueError):
            build(["r-1"])


if __name__ == "__main__":
    unittest.main()
