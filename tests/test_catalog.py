import os
import shutil
import tempfile
import unittest

from partitioncatalog import catalog, list_partitions, register


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_register_creates_dir(self):
        path = register({"dataset": "orders", "name": "dt=2026-01-01"})
        self.assertTrue(os.path.isdir(path))

    def test_list_returns_names(self):
        register({"dataset": "orders", "name": "dt=2026-01-01"})
        self.assertEqual(list_partitions("orders"), ["dt=2026-01-01"])

    def test_no_metadata(self):
        register({"dataset": "orders", "name": "dt=2026-01-01"})
        self.assertEqual(os.listdir(os.path.join(catalog.ROOT, "orders", "dt=2026-01-01")), [])

    def test_missing_dataset_empty(self):
        self.assertEqual(list_partitions("nope"), [])
