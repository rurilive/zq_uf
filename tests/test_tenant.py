import os
import shutil
import tempfile
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
