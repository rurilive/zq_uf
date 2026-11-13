import unittest

from idemwrite import (
    BatchWriteError,
    drop_store,
    get_stats,
    reset_stats,
    upsert,
)
from idemwrite.core import VERSION_FIELD, _state_of


class IdemwriteTest(unittest.TestCase):
    def tearDown(self):
        # 注册表按 id(store) 索引，防止用例间对象 id 复用造成统计串扰。
        for store in list(getattr(self, "_stores", [])):
            drop_store(store)

    def _new_store(self):
        store = {}
        self._stores = getattr(self, "_stores", []) + [store]
        return store

    def test_insert(self):
        store = upsert([{"id": 1, "v": "a"}], ["id"])
        self.assertEqual(store[(1,)], {"id": 1, "v": "a"})

    def test_overwrite(self):
        store = upsert([{"id": 1, "v": "a"}], ["id"])
        upsert([{"id": 1, "v": "b"}], ["id"], store)
        self.assertEqual(store[(1,)]["v"], "b")

    def test_composite_key(self):
        store = upsert([{"a": 1, "b": 2}], ["a", "b"])
        self.assertIn((1, 2), store)

    def test_duplicate_insert_is_idempotent(self):
        rows = [{"id": 1, "v": "a", "version": 1}]
        store = self._new_store()
        upsert(rows, ["id"], store)
        # 整批重跑：同版本不重复覆盖，计为 duplicate。
        upsert([dict(row) for row in rows], ["id"], store)
        self.assertEqual(store[(1,)], {"id": 1, "v": "a", "version": 1})
        stats = get_stats(store)
        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(stats["duplicates"], 1)
        self.assertEqual(stats["stale"], 0)

    def test_version_rollback_is_ignored(self):
        store = self._new_store()
        upsert([{"id": 1, "v": "new", "version": 5}], ["id"], store)
        # 旧版本到达：忽略，不回退数据，计为 stale。
        upsert([{"id": 1, "v": "old", "version": 3}], ["id"], store)
        self.assertEqual(store[(1,)], {"id": 1, "v": "new", "version": 5})
        self.assertEqual(get_stats(store)["stale"], 1)
        # 新版本到达：正常覆盖。
        upsert([{"id": 1, "v": "next", "version": 6}], ["id"], store)
        self.assertEqual(store[(1,)]["v"], "next")
        self.assertEqual(get_stats(store)["updated"], 1)

    def test_batch_partial_failure_can_retry(self):
        rows = [
            {"id": 1, "v": "a", "version": 1},
            {"id": 2, "v": "b", "version": 1},
            {"id": 3, "v": "c", "version": 1},
            {"id": 4, "v": "d", "version": 1},
        ]
        store = self._new_store()

        def fail_first_attempt(batch_index):
            if batch_index == 1 and not getattr(fail_first_attempt, "fired", False):
                fail_first_attempt.fired = True
                raise RuntimeError("simulated commit failure")

        # 每批 2 行：批次 0 先成功，批次 1 首次提交失败，批次原子回滚。
        with self.assertRaises(BatchWriteError) as ctx:
            upsert(rows, ["id"], store, batch_size=2, on_batch=fail_first_attempt)
        self.assertEqual(ctx.exception.batch_index, 1)
        self.assertEqual([key for key in sorted(store)], [(1,), (2,)])
        self.assertNotIn((3,), store)

        # 重跑同一批数据：已成功批次按重复跳过，失败批次重试成功。
        upsert(rows, ["id"], store, batch_size=2)
        self.assertEqual([key for key in sorted(store)],
                         [(1,), (2,), (3,), (4,)])
        stats = get_stats(store)
        self.assertEqual(stats["inserted"], 4)
        self.assertEqual(stats["duplicates"], 2)
        self.assertEqual(stats["batches_failed"], 1)
        self.assertEqual(stats["batches_committed"], 3)

    def test_unique_conflict_becomes_version_comparison(self):
        store = self._new_store()
        upsert([{"id": 1, "v": "base", "version": 2}], ["id"], store)

        # 模拟并发：条件更新期间另一线程先写入了更高版本。
        def concurrent_writer(batch_index):
            if not getattr(concurrent_writer, "fired", False):
                concurrent_writer.fired = True
                # 直接改存储与版本表，模拟持锁期间其他线程的并发提交。
                row = {"id": 1, "v": "concurrent", VERSION_FIELD: 4}
                store[(1,)] = row
                _state_of(store).versions[(1,)] = 4

        # 旧版本冲突：重读再判后忽略，不抛唯一键冲突、不整批失败。
        upsert([{"id": 1, "v": "stale", "version": 3}], ["id"],
               store, on_batch=concurrent_writer)
        self.assertEqual(store[(1,)]["v"], "concurrent")
        stats = get_stats(store)
        self.assertEqual(stats["stale"], 1)
        self.assertEqual(stats["conflict_retries"], 1)

        # 新版本冲突：重读后条件更新成功。
        upsert([{"id": 1, "v": "winner", "version": 5}], ["id"], store)
        self.assertEqual(store[(1,)], {"id": 1, "v": "winner", "version": 5})

    def test_concurrent_writers_highest_version_wins(self):
        import threading

        store = self._new_store()
        reset_stats(store)
        keys = ["id"]
        key_count = 8
        worker_count = 4
        barrier = threading.Barrier(worker_count)
        errors = []

        def worker(version):
            try:
                barrier.wait()
                rows = [
                    {"id": index, "v": "w%d" % version, "version": version}
                    for index in range(key_count)
                ]
                upsert(rows, keys, store, batch_size=3)
            except BaseException as exc:  # 并发写不应让任何线程失败
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(version,))
            for version in range(1, worker_count + 1)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        for index in range(key_count):
            self.assertEqual(
                store[(index,)],
                {"id": index, "v": "w%d" % worker_count,
                 "version": worker_count},
            )
        stats = get_stats(store)
        self.assertEqual(stats["inserted"], key_count)
        self.assertEqual(
            stats["updated"] + stats["duplicates"] + stats["stale"],
            key_count * (worker_count - 1),
        )
