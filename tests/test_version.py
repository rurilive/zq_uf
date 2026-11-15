import json
import os
import shutil
import tempfile
import threading
import unittest

from tableversion import TableVersion, IntegrityError, checksum_of, commit, reset, rollback


class VersionTest(unittest.TestCase):
    def setUp(self):
        # 模块级门面的状态在用例间隔离，旧断言本身保持不变。
        reset()

    def test_commit_overwrites(self):
        commit({"name": "t1", "rows": [1, 2]})
        self.assertEqual(commit({"name": "t1", "rows": [3]})["rows"], 1)

    def test_commit_has_no_version(self):
        self.assertIsNone(commit({"name": "t2", "rows": []})["version"])

    def test_rollback_does_nothing(self):
        self.assertFalse(rollback(1)["rolled_back"])


class VersionedStoreTest(unittest.TestCase):
    def setUp(self):
        self.tv = TableVersion(committer="alice")

    def tearDown(self):
        self.tv.close()

    # -- 版本生成 -----------------------------------------------------
    def test_commit_generates_versions_and_checksums(self):
        r1 = self.tv.commit({"name": "orders", "rows": [1, 2, 3]})
        r2 = self.tv.commit({"name": "orders", "rows": [4, 5]})
        self.assertEqual(r1["version"], 1)
        self.assertEqual(r2["version"], 2)
        self.assertIsNone(r1["parent"])
        self.assertEqual(r2["parent"], 1)
        self.assertEqual(r1["checksum"], checksum_of([1, 2, 3]))
        self.assertRegex(r1["checksum"], r"^[0-9a-f]{64}$")
        self.assertEqual(r1["committer"], "alice")
        # 历史版本不被覆盖。
        head = self.tv.head("orders")
        self.assertEqual(head["rows"], [4, 5])
        self.assertEqual(self.tv.history("orders")[1]["version"], 1)

    def test_empty_commit_creates_no_version(self):
        r = self.tv.commit({"name": "empty_t", "rows": []})
        self.assertIsNone(r["version"])
        self.assertIsNone(r["checksum"])
        self.assertEqual(r["rows"], 0)
        self.assertEqual(self.tv.history("empty_t"), [])

    def test_versions_are_per_table(self):
        self.tv.commit({"name": "a", "rows": [1]})
        rb = self.tv.commit({"name": "b", "rows": [2]})
        self.assertEqual(rb["version"], 1)
        self.assertIsNone(rb["parent"])

    def test_chain_records_parent_and_committer(self):
        self.tv.commit({"name": "c", "rows": [1]}, committer="u1")
        self.tv.commit({"name": "c", "rows": [2]}, committer="u2")
        chain = self.tv.history("c")
        self.assertEqual([e["version"] for e in chain], [2, 1])
        self.assertEqual(chain[0]["parent"], 1)
        self.assertIsNone(chain[1]["parent"])
        self.assertEqual([e["committer"] for e in chain], ["u2", "u1"])
        for entry in chain:
            self.assertRegex(entry["checksum"], r"^[0-9a-f]{64}$")

    def test_stored_snapshot_isolated_from_caller_mutation(self):
        rows = [{"id": 1}]
        self.tv.commit({"name": "iso", "rows": rows})
        rows[0]["id"] = 999
        self.assertEqual(self.tv.head("iso")["rows"], [{"id": 1}])

    # -- 结构关联 -----------------------------------------------------
    def test_schema_inferred_and_attached_to_version(self):
        r = self.tv.commit({"name": "people", "rows": [{"id": 1, "name": "a"}]})
        self.assertEqual([c["name"] for c in r["schema"]], ["id", "name"])
        meta_schema = self.tv.history("people")[0]
        self.assertRegex(meta_schema["checksum"], r"^[0-9a-f]{64}$")

    def test_schema_change_recorded_with_version(self):
        self.tv.commit({"name": "p", "rows": [{"id": 1}]})
        r2 = self.tv.commit({"name": "p", "rows": [{"id": 2, "email": "x@y"}]})
        self.assertEqual(r2["schema_changed"]["added"], ["email"])
        r3 = self.tv.commit({"name": "p", "rows": [{"id": 3}]})
        self.assertEqual(r3["schema_changed"]["removed"], ["email"])
        r4 = self.tv.commit(
            {"name": "p", "rows": [{"id": 4}], "schema": [{"name": "id", "type": "str"}]}
        )
        self.assertEqual(r4["schema_changed"]["changed"],
                         [{"name": "id", "from": "any", "to": "str"}])

    def test_unchanged_schema_has_empty_diff(self):
        self.tv.commit({"name": "s", "rows": [{"id": 1}]})
        r2 = self.tv.commit({"name": "s", "rows": [{"id": 2}]})
        self.assertEqual(r2["schema_changed"], {})

    # -- 回滚 ---------------------------------------------------------
    def test_rollback_restores_data_and_keeps_history(self):
        self.tv.commit({"name": "r", "rows": [1, 2]})
        self.tv.commit({"name": "r", "rows": [3, 4, 5]})
        result = self.tv.rollback(1, table="r")
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["restored"], 2)
        self.assertEqual(self.tv.head("r")["rows"], [1, 2])
        # 当前版本与历史版本都保留：链上有 3 个版本。
        versions = [e["version"] for e in self.tv.history("r")]
        self.assertEqual(versions, [3, 2, 1])
        self.assertEqual(result["tables"][0]["table"], "r")

    def test_rollback_recovers_schema(self):
        self.tv.commit({"name": "rs", "rows": [{"id": 1}]})
        self.tv.commit({"name": "rs", "rows": [{"id": 2, "email": "e"}]})
        self.tv.rollback(1, table="rs")
        self.assertEqual([c["name"] for c in self.tv.head("rs")["schema"]], ["id"])

    def test_rollback_unknown_version_reports_failure(self):
        self.tv.commit({"name": "x", "rows": [1]})
        result = self.tv.rollback(99, table="x")
        self.assertFalse(result["rolled_back"])
        self.assertEqual(result["tables"][0]["reason"], "版本不存在")

    def test_rollback_validates_chain_before_restoring(self):
        self.tv.commit({"name": "v", "rows": [1]})
        self.tv.commit({"name": "v", "rows": [2, 3]})
        # 直接篡改数据区，模拟落盘数据损坏。
        key = ("v", 1)
        self.tv.storage.data[key] = {"rows": [999]}
        with self.assertRaises(IntegrityError):
            self.tv.rollback(1, table="v")
        # 校验失败时不允许写入恢复点，当前数据保持原样。
        self.assertEqual(self.tv.head("v")["rows"], [2, 3])
        self.assertEqual(self.tv.storage.list_versions("v"), [1, 2])

    def test_validate_detects_broken_parent_pointer(self):
        self.tv.commit({"name": "pp", "rows": [1]})
        self.tv.commit({"name": "pp", "rows": [2]})
        self.tv.storage.meta[("pp", 2)]["parent"] = 99
        with self.assertRaises(IntegrityError):
            self.tv.validate("pp")

    def test_partial_rollback_multiple_tables(self):
        self.tv.commit({"name": "t1", "rows": ["a1"]})
        self.tv.commit({"name": "t1", "rows": ["a2"]})
        self.tv.commit({"name": "t2", "rows": ["b1"]})
        self.tv.commit({"name": "t2", "rows": ["b2"]})
        result = self.tv.rollback(1, tables=["t1", "t2"])
        self.assertTrue(result["rolled_back"])
        self.assertEqual(self.tv.head("t1")["rows"], ["a1"])
        self.assertEqual(self.tv.head("t2")["rows"], ["b1"])
        self.assertEqual([d["table"] for d in result["tables"]], ["t1", "t2"])

    # -- 并发 ---------------------------------------------------------
    def test_concurrent_commits_serialized_in_order(self):
        n = 20

        def worker(i):
            self.tv.commit({"name": "q", "rows": [i]}, committer="w%d" % i)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        versions = self.tv.storage.list_versions("q")
        self.assertEqual(versions, list(range(1, n + 1)))
        self.assertEqual(len(self.tv.history("q")), n)
        # 每个版本的数据都完整保留，没有互相覆盖。
        payloads = {
            v: self.tv.storage.read_data("q", v)["rows"][0] for v in versions
        }
        self.assertEqual(sorted(payloads.values()), list(range(n)))
        self.assertEqual(self.tv.head("q")["version"], n)

    # -- 归档 ---------------------------------------------------------
    def test_archive_expired_versions_and_block_rollback(self):
        for i in range(4):
            self.tv.commit({"name": "ar", "rows": [i]})
        result = self.tv.archive(keep=2, table="ar")
        self.assertEqual(result["count"], 2)
        self.assertEqual(sorted(d["version"] for d in result["archived"]), [1, 2])
        self.assertEqual(self.tv.storage.list_versions("ar"), [3, 4])
        self.assertEqual(self.tv.list_archived("ar"), [1, 2])
        self.assertEqual(self.tv.head("ar")["version"], 4)
        with self.assertRaises(IntegrityError):
            self.tv.rollback(1, table="ar")
        # 存活版本仍可回滚。
        self.assertTrue(self.tv.rollback(3, table="ar")["rolled_back"])

    def test_archive_keeps_head_consistent(self):
        self.tv.commit({"name": "ah", "rows": [1]})
        self.tv.commit({"name": "ah", "rows": [2]})
        self.tv.archive(keep=0, table="ah")
        self.assertEqual(self.tv.storage.list_versions("ah"), [])
        self.assertIsNone(self.tv.head("ah"))


class FileStorageTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tableversion-")
        self.tv = TableVersion(root=self.root, committer="bob")

    def tearDown(self):
        self.tv.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_metadata_and_data_stored_separately(self):
        self.tv.commit({"name": "f", "rows": [{"id": 1}]})
        meta_dir = os.path.join(self.root, "metadata", "f")
        data_dir = os.path.join(self.root, "data", "f")
        self.assertTrue(os.path.isdir(meta_dir))
        self.assertTrue(os.path.isdir(data_dir))
        meta_files = os.listdir(meta_dir)
        data_files = os.listdir(data_dir)
        self.assertEqual(meta_files, ["v000001.json"])
        self.assertEqual(data_files, ["v000001.json"])
        # 元数据文件不含行数据，数据文件不含链信息。
        with open(os.path.join(meta_dir, meta_files[0]), encoding="utf-8") as f:
            import json
            meta = json.load(f)
        self.assertNotIn("rows_payload", meta)
        self.assertEqual(meta["checksum"], checksum_of([{"id": 1}]))

    def test_state_persists_across_instances(self):
        self.tv.commit({"name": "p", "rows": [1, 2]})
        self.tv.commit({"name": "p", "rows": [3]})
        self.tv.close()
        tv2 = TableVersion(root=self.root)
        try:
            self.assertEqual(tv2.head("p")["version"], 2)
            self.assertEqual(tv2.head("p")["rows"], [3])
            result = tv2.rollback(1, table="p")
            self.assertTrue(result["rolled_back"])
            self.assertEqual(tv2.head("p")["rows"], [1, 2])
        finally:
            tv2.close()

    def test_archive_moves_files_into_archive_tree(self):
        self.tv.commit({"name": "af", "rows": [1]})
        self.tv.commit({"name": "af", "rows": [2]})
        self.tv.archive(keep=1, table="af")
        archived_meta = os.path.join(self.root, "archive", "metadata", "af", "v000001.json")
        archived_data = os.path.join(self.root, "archive", "data", "af", "v000001.json")
        self.assertTrue(os.path.exists(archived_meta))
        self.assertTrue(os.path.exists(archived_data))
        self.assertFalse(os.path.exists(os.path.join(self.root, "metadata", "af", "v000001.json")))


if __name__ == "__main__":
    unittest.main()
