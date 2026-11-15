import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from sitebudget2 import give_back, reclaim, stats, take

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _child_take(d, site, n):
    os.environ["SITEBUDGET2_DIR"] = d
    from sitebudget2 import take as t
    t(site, n)


def _child_give_back(d, site, n):
    os.environ["SITEBUDGET2_DIR"] = d
    from sitebudget2 import give_back as g
    g(site, n)


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        os.environ["SITEBUDGET2_DIR"] = self._dir

    def tearDown(self):
        os.environ.pop("SITEBUDGET2_DIR", None)
        shutil.rmtree(self._dir, ignore_errors=True)

    def _write_config(self, budgets, lease_seconds=60):
        with open(os.path.join(self._dir, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"budgets": budgets, "lease_seconds": lease_seconds}, fh)

    def _run_child(self, target, *args):
        ctx = multiprocessing.get_context("fork")
        p = ctx.Process(target=target, args=args)
        p.start()
        p.join()
        self.assertEqual(p.exitcode, 0)

    def test_take_accumulates_locally(self):
        take("a", 2)
        self.assertEqual(take("a", 3)["used"], 5)

    def test_give_back_reduces(self):
        take("b", 4)
        self.assertEqual(give_back("b", 1)["used"], 3)

    def test_take_never_waits(self):
        self.assertEqual(take("c", 100)["wait"], 0)

    def test_cross_process_take(self):
        self._write_config({"x": 10})
        self._run_child(_child_take, self._dir, "x", 3)
        self.assertEqual(take("x", 1)["used"], 4)

    def test_cross_process_give_back(self):
        self._write_config({"x": 10})
        take("x", 5)
        self._run_child(_child_give_back, self._dir, "x", 2)
        self.assertEqual(stats()["sites"]["x"]["used"], 3)

    def test_crash_reclaim(self):
        self._write_config({"x": 10}, lease_seconds=0.3)
        env = dict(os.environ, SITEBUDGET2_DIR=self._dir,
                   PYTHONPATH=REPO_ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
        p = subprocess.Popen(
            [sys.executable, "-c",
             "from sitebudget2 import take; import time; take('x', 4); time.sleep(30)"],
            env=env)
        for _ in range(100):  # 等子进程拿到额度
            if stats()["sites"].get("x", {}).get("used") == 4:
                break
            time.sleep(0.05)
        p.kill()
        p.wait()
        self.assertEqual(stats()["sites"]["x"]["used"], 4)  # 崩溃不自动释放
        time.sleep(0.5)  # 等租约到期
        self.assertEqual(sum(e["n"] for e in reclaim()), 4)
        self.assertEqual(stats()["sites"]["x"]["used"], 0)

    def test_wait_when_over_budget(self):
        self._write_config({"x": 5}, lease_seconds=10)
        take("x", 5)
        r = take("x", 1)
        self.assertEqual(r["taken"], 0)
        self.assertGreater(r["wait"], 0)
        self.assertLessEqual(r["wait"], 10)

    def test_wait_never_when_n_exceeds_budget(self):
        self._write_config({"x": 5})
        self.assertEqual(take("x", 6)["wait"], -1)

    def test_hot_reload_budget(self):
        self._write_config({"x": 5})
        take("x", 5)
        self.assertEqual(take("x", 1)["taken"], 0)
        self._write_config({"x": 7})
        self.assertEqual(take("x", 1)["taken"], 1)

    def test_reconcile_across_processes(self):
        self._write_config({"x": 20})
        take("x", 5)
        self._run_child(_child_take, self._dir, "x", 3)
        self._run_child(_child_give_back, self._dir, "x", 1)
        give_back("x", 2)
        s = stats()
        self.assertEqual(s["sites"]["x"]["used"], 5)
        self.assertTrue(s["balanced"])


if __name__ == "__main__":
    unittest.main()
