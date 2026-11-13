import unittest

from cdcmerge import Merger


class CdcmergeTest(unittest.TestCase):
    def test_insert_and_update(self):
        merger = Merger()
        merger.apply([{"op": "insert", "key": "a", "value": 1}])
        merger.apply([{"op": "update", "key": "a", "value": 2}])
        self.assertEqual(merger.snapshot(), {"a": 2})

    def test_delete(self):
        merger = Merger()
        merger.apply([{"op": "insert", "key": "a", "value": 1}])
        merger.apply([{"op": "delete", "key": "a"}])
        self.assertEqual(merger.snapshot(), {})

    def test_new_merger_is_empty(self):
        self.assertEqual(Merger().snapshot(), {})

    def test_out_of_order_updates_keep_highest_version(self):
        merger = Merger()
        merger.apply([
            {"op": "update", "key": "a", "value": 3, "version": 3},
            {"op": "update", "key": "a", "value": 1, "version": 1},
            {"op": "update", "key": "a", "value": 2, "version": 2},
        ])
        self.assertEqual(merger.snapshot(), {"a": 3})

        # 更晚到达的更高版本仍然可以收敛。
        merger.apply([{"op": "update", "key": "a", "value": 4, "version": 4}])
        self.assertEqual(merger.snapshot(), {"a": 4})

    def test_duplicate_delivery_is_idempotent(self):
        merger = Merger()
        events = [
            {"op": "insert", "key": "a", "value": 1, "version": 1,
             "txn": 10, "seq": 1},
            {"op": "update", "key": "a", "value": 2, "version": 2,
             "txn": 10, "seq": 2},
        ]
        merger.apply(events)
        merger.apply({"op": "commit", "txn": 10})
        first = merger.snapshot()

        # 同一批事件和 commit 重复投递，快照与内部收敛结果保持不变。
        merger.apply(events)
        merger.apply({"op": "commit", "txn": 10})
        self.assertEqual(merger.snapshot(), first)
        self.assertEqual(merger.snapshot(), {"a": 2})

    def test_duplicate_event_inside_open_transaction(self):
        merger = Merger()
        event = {"op": "update", "key": "a", "value": 1, "version": 1,
                 "txn": 10, "seq": 1}
        merger.apply([event, event])
        merger.apply({"op": "commit", "txn": 10})
        self.assertEqual(merger.snapshot(), {"a": 1})

    def test_delete_tombstone_blocks_stale_resurrection(self):
        merger = Merger()
        merger.apply([
            {"op": "insert", "key": "a", "value": 1, "version": 1},
            {"op": "delete", "key": "a", "version": 3},
        ])
        self.assertEqual(merger.snapshot(), {})

        # 晚到的旧版本插入/更新不能复活已删除的记录。
        merger.apply([{"op": "update", "key": "a", "value": 9, "version": 2}])
        self.assertEqual(merger.snapshot(), {})

        # 更高版本的重新插入可以让记录回归。
        merger.apply([{"op": "insert", "key": "a", "value": 4, "version": 4}])
        self.assertEqual(merger.snapshot(), {"a": 4})

    def test_transaction_changes_are_atomic(self):
        merger = Merger()
        merger.apply([{"op": "insert", "key": "old", "value": 0,
                       "version": 1}])

        # 事务未提交前，缓冲中的变更不出现在快照里。
        merger.apply([
            {"op": "update", "key": "old", "value": 1, "version": 2,
             "txn": 7, "seq": 1},
            {"op": "insert", "key": "new", "value": 2, "version": 1,
             "txn": 7, "seq": 2},
            {"op": "delete", "key": "gone", "version": 1,
             "txn": 7, "seq": 3},
        ])
        self.assertEqual(merger.snapshot(), {"old": 0})

        # commit 后同一事务的多条变更整体可见。
        merger.apply({"op": "commit", "txn": 7})
        self.assertEqual(merger.snapshot(), {"old": 1, "new": 2})

    def test_interleaved_transactions_commit_independently(self):
        merger = Merger()
        merger.apply([
            {"op": "update", "key": "a", "value": 1, "version": 1,
             "txn": 1, "seq": 1},
            {"op": "update", "key": "b", "value": 1, "version": 1,
             "txn": 2, "seq": 1},
        ])
        merger.apply({"op": "commit", "txn": 1})
        self.assertEqual(merger.snapshot(), {"a": 1})
        merger.apply({"op": "commit", "txn": 2})
        self.assertEqual(merger.snapshot(), {"a": 1, "b": 1})
