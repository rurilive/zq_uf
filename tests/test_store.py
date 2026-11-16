import os
import tempfile
import unittest

from wafregress import Engine, configure, list_runs, regress, report, reset
from wafregress.cases import load_case_files
from wafregress.store import FileStore, MemoryStore

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "cases")

RULES_V1 = [
    {"id": "sqli", "pattern": "union select"},
    {"id": "xss", "pattern": "<script>"},
]
RULES_V2 = [
    {"id": "sqli", "pattern": "union select"},
    {"id": "xss", "pattern": "<"},
]


class MemoryStoreTest(unittest.TestCase):
    def test_reset_clears_runs_keeps_cases(self):
        engine = Engine(store=MemoryStore(), cases=load_case_files([DATA_DIR]))
        engine.regress(RULES_V1)
        self.assertEqual(len(engine.list_runs()), 1)
        engine.reset()
        self.assertEqual(engine.list_runs(), [])
        # 用例集不受 reset 影响
        self.assertEqual(len(engine.cases), 7)


class FileStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store_dir = os.path.join(self.tmp.name, "regress-store")

    def tearDown(self):
        self.tmp.cleanup()

    def _engine(self):
        return Engine(
            store=FileStore(self.store_dir),
            cases=load_case_files([DATA_DIR]),
        )

    def test_runs_persisted_and_reloaded(self):
        engine = self._engine()
        engine.regress(RULES_V1)
        engine.regress(RULES_V2)
        self.assertEqual(len(engine.list_runs()), 2)

        # 用一个全新的引擎指向同一目录，历史结果仍可查
        reloaded = self._engine()
        runs = reloaded.list_runs()
        self.assertEqual([item["run_id"] for item in runs], [1, 2])
        first = reloaded.report(run_id=1)
        second = reloaded.report(run_id=2)
        self.assertEqual(first["summary"]["false_positive"], 0)
        self.assertEqual(second["summary"]["false_positive"], 2)

        # 历史两轮仍可对比
        diff = reloaded.compare(1, 2)
        self.assertEqual(diff["summary_delta"]["false_positive"], 2)

    def test_run_ids_continue_after_reload(self):
        engine = self._engine()
        engine.regress(RULES_V1)
        reloaded = self._engine()
        out = reloaded.regress(RULES_V2)
        self.assertEqual(out["run_id"], 2)

    def test_index_and_run_files_on_disk(self):
        engine = self._engine()
        engine.regress(RULES_V1)
        self.assertTrue(
            os.path.isfile(os.path.join(self.store_dir, "index.json"))
        )
        self.assertTrue(
            os.path.isfile(os.path.join(self.store_dir, "runs", "run_1.json"))
        )

    def test_clear_removes_persisted_files(self):
        engine = self._engine()
        engine.regress(RULES_V1)
        engine.reset()
        self.assertEqual(os.listdir(os.path.join(self.store_dir, "runs")), [])
        reloaded = self._engine()
        self.assertEqual(reloaded.list_runs(), [])


class ConfigureTest(unittest.TestCase):
    def setUp(self):
        reset()

    def tearDown(self):
        configure()  # 回到纯内存默认引擎
        reset()

    def test_configure_file_store_for_module_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            configure(store_path=os.path.join(tmp, "store"))
            regress(RULES_V1)
            self.assertEqual(len(list_runs()), 1)
            self.assertEqual(report()["rules"], 2)
            # 落盘文件存在
            self.assertTrue(
                os.path.isfile(os.path.join(tmp, "store", "index.json"))
            )


if __name__ == "__main__":
    unittest.main()
