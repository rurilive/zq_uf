import os
import shutil
import tempfile
import threading
import unittest

from tenantcrawl import RESULTS, run
from tenantcrawl import tenant as module


class TenantTest(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_same_result_file(self):
        a = run("a", [{"id": 1}])
        b = run("b", [{"id": 2}])
        self.assertEqual(a, b)

    def test_tenants_see_each_other(self):
        run("a", [{"id": 1}])
        run("b", [{"id": 2}])
        rows = module.read_all()
        self.assertEqual({row["tenant"] for row in rows}, {"a", "b"})

    def test_shared_config(self):
        run("a", [{"id": 1}])
        run("b", [{"id": 2}])
        self.assertEqual({row["workers"] for row in module.read_all()}, {4})


class IsolatedTenantTest(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)
        module.reset()

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.dir, ignore_errors=True)
        module.reset()


class DirectoryIsolationTest(IsolatedTenantTest):
    def test_per_tenant_result_directories(self):
        module.run("alpha", [{"id": 1}])
        module.run("beta", [{"id": 2}])
        path_a = os.path.join(RESULTS, "alpha", "out.json")
        path_b = os.path.join(RESULTS, "beta", "out.json")
        self.assertNotEqual(path_a, path_b)
        self.assertTrue(os.path.isfile(path_a))
        self.assertTrue(os.path.isfile(path_b))

    def test_results_only_contain_own_tenant(self):
        module.run("alpha", [{"id": 1}])
        module.run("beta", [{"id": 2}])
        rows_a = module.read_results("alpha")
        rows_b = module.read_results("beta")
        self.assertEqual({row["tenant"] for row in rows_a}, {"alpha"})
        self.assertEqual({row["tenant"] for row in rows_b}, {"beta"})
        self.assertEqual([row["task"] for row in rows_a], [{"id": 1}])
        self.assertEqual([row["task"] for row in rows_b], [{"id": 2}])


class QuotaIsolationTest(IsolatedTenantTest):
    def test_quota_exceeded_raises(self):
        module.configure("small", quota=1)
        with self.assertRaises(module.QuotaExceededError):
            module.run("small", [{"id": 1}, {"id": 2}])

    def test_quota_isolated_between_tenants(self):
        module.configure("small", quota=1)
        module.configure("large", quota=10)
        with self.assertRaises(module.QuotaExceededError):
            module.run("small", [{"id": 1}, {"id": 2}])
        self.assertEqual(module.run("large", [{"id": 1}, {"id": 2}]), 2)

    def test_quota_not_consumed_by_other_tenant(self):
        module.configure("qx", quota=2)
        module.configure("qy", quota=2)
        self.assertEqual(module.run("qx", [{"id": 1}, {"id": 2}]), 2)
        self.assertEqual(module.run("qy", [{"id": 1}, {"id": 2}]), 2)

    def test_zero_quota_rejects_any_task(self):
        module.configure("empty", quota=0)
        with self.assertRaises(module.QuotaExceededError):
            module.run("empty", [{"id": 1}])


class UnauthorizedReadTest(IsolatedTenantTest):
    def test_cross_tenant_path_rejected(self):
        module.run("victim", [{"id": 1}])
        module.run("attacker", [{"id": 2}])
        with self.assertRaises(module.TenantAccessError):
            module.read_results("attacker", "../victim/out.json")

    def test_traversal_tenant_name_rejected(self):
        with self.assertRaises(module.InvalidTenantError):
            module.run("../victim", [{"id": 1}])

    def test_read_results_scoped_to_own_tenant(self):
        module.run("victim", [{"id": 1}])
        module.run("attacker", [{"id": 2}])
        rows = module.read_results("attacker")
        self.assertEqual({row["tenant"] for row in rows}, {"attacker"})

    def test_missing_results_raises(self):
        with self.assertRaises(FileNotFoundError):
            module.read_results("ghost")


class ParallelTenantsTest(IsolatedTenantTest):
    def test_tenants_run_in_parallel_without_interference(self):
        module.configure("pa", workers=2, rate_per_sec=1000)
        module.configure("pb", workers=2, rate_per_sec=1000)
        errors = []

        def work(name, base):
            try:
                done = module.run(name, [{"id": base + i} for i in range(5)])
                self.assertEqual(done, 5)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=work, args=("pa", 0)),
            threading.Thread(target=work, args=("pb", 100)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        rows_a = module.read_results("pa")
        rows_b = module.read_results("pb")
        self.assertEqual({row["tenant"] for row in rows_a}, {"pa"})
        self.assertEqual({row["tenant"] for row in rows_b}, {"pb"})
        self.assertEqual([row["task"]["id"] for row in rows_a], [0, 1, 2, 3, 4])
        self.assertEqual(
            [row["task"]["id"] for row in rows_b], [100, 101, 102, 103, 104]
        )
        self.assertEqual(module.stats("pa")["tasks_completed"], 5)
        self.assertEqual(module.stats("pb")["tasks_completed"], 5)


class ValidationTest(IsolatedTenantTest):
    def test_empty_tenant_rejected(self):
        with self.assertRaises(module.InvalidTenantError):
            module.run("", [{"id": 1}])

    def test_separator_tenant_rejected(self):
        with self.assertRaises(module.InvalidTenantError):
            module.run("a/b", [{"id": 1}])

    def test_non_mapping_task_rejected(self):
        with self.assertRaises(module.InvalidTaskError):
            module.run("t1", ["oops"])

    def test_unserializable_task_rejected(self):
        with self.assertRaises(module.InvalidTaskError):
            module.run("t1", [{"id": object()}])

    def test_non_iterable_tasks_rejected(self):
        with self.assertRaises(module.InvalidTaskError):
            module.run("t1", None)

    def test_empty_task_list_returns_zero(self):
        self.assertEqual(module.run("t1", []), 0)

    def test_invalid_workers_rejected(self):
        with self.assertRaises(ValueError):
            module.configure("t1", workers=0)

    def test_invalid_rate_rejected(self):
        with self.assertRaises(ValueError):
            module.configure("t1", rate_per_sec=0)

    def test_unknown_config_key_rejected(self):
        with self.assertRaises(ValueError):
            module.configure("t1", timeout=5)
