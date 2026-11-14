import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import snapshotgc.gc
from snapshotgc import (
    abort_backup,
    begin_backup,
    commit_backup,
    create_snapshot,
    delete_snapshot,
    gc,
    pin,
    read_snapshot,
    reset,
    write_block,
)

gc_mod = sys.modules["snapshotgc.gc"]


class GcTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_removes_nothing(self):
        self.assertEqual(gc(1)["removed"], 0)

    def test_pin_has_no_effect(self):
        self.assertFalse(pin("snap-1")["pinned"])

    def test_no_refcount(self):
        self.assertNotIn("refs", gc(1))


def _backup(name, parent=None, blocks=()):
    begin_backup(name, parent=parent)
    bids = [write_block(name, data) for data in blocks]
    commit_backup(name)
    return bids


class RefCountTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_shared_blocks_keep_refs(self):
        bids = _backup("a1", blocks=[b"x", b"y"])
        create_snapshot("a2", parent="a1")
        for bid in bids:
            self.assertEqual(gc_mod._state["blocks"][bid]["refs"], 2)

    def test_delete_only_decrements(self):
        bids = _backup("a1", blocks=[b"x"])
        create_snapshot("a2", parent="a1")
        delete_snapshot("a1")
        self.assertEqual(gc_mod._state["blocks"][bids[0]]["refs"], 1)
        self.assertEqual(gc(5)["removed"], 0)

    def test_zero_ref_collected(self):
        _backup("a1", blocks=[b"x", b"y"])
        delete_snapshot("a1")
        result = gc(0)
        self.assertEqual(result["removed"], 2)
        self.assertEqual(result["freed"], 2)
        self.assertEqual(gc_mod._state["blocks"], {})

    def test_parent_deleted_child_readable(self):
        _backup("a1", blocks=[b"base"])
        _backup("a2", parent="a1", blocks=[b"child"])
        delete_snapshot("a1")
        data = read_snapshot("a2")
        self.assertEqual(sorted(data.values()), [b"base", b"child"])
        gc(1)
        self.assertEqual(sorted(read_snapshot("a2").values()), [b"base", b"child"])


class LeaseTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_pin_protects_snapshot(self):
        _backup("a1", blocks=[b"x"])
        _backup("a2", parent="a1", blocks=[b"y"])
        self.assertTrue(pin("a1")["pinned"])
        result = gc(1)
        self.assertEqual(result["removed"], 0)
        self.assertGreaterEqual(result["skipped"], 1)
        self.assertEqual(list(read_snapshot("a1").values()), [b"x"])

    def test_lease_expiry_allows_gc(self):
        _backup("a1", blocks=[b"x"])
        pin("a1", ttl=0.05)
        time.sleep(0.1)
        result = gc(0)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["freed"], 1)

    def test_pin_unknown_tag_not_pinned(self):
        self.assertFalse(pin("nope")["pinned"])


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_registered_blocks_skipped(self):
        _backup("a1", blocks=[b"base"])
        begin_backup("a2", parent="a1")
        write_block("a2", b"pending")
        result = gc(0)
        self.assertEqual(result["removed"], 0)
        self.assertGreaterEqual(result["skipped"], 1)
        commit_backup("a2")
        self.assertEqual(sorted(read_snapshot("a2").values()), [b"base", b"pending"])

    def test_concurrent_backup_and_gc(self):
        errors = []

        def run_backups():
            try:
                for i in range(10):
                    name = "snap-%d-%d" % (threading.get_ident(), i)
                    begin_backup(name)
                    write_block(name, b"data-%d" % i)
                    commit_backup(name)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def run_gc():
            try:
                for _ in range(10):
                    gc(1)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=run_backups) for _ in range(3)]
        threads += [threading.Thread(target=run_gc) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        for bid, meta in gc_mod._state["blocks"].items():
            self.assertEqual(meta["status"], "committed")
            self.assertGreater(meta["refs"], 0)
            self.assertTrue(os.path.exists(gc_mod._block_path(bid)))
        for name in gc_mod._state["snapshots"]:
            self.assertTrue(read_snapshot(name))

    def test_abort_releases_refs(self):
        _backup("a1", blocks=[b"base"])
        begin_backup("a2", parent="a1")
        write_block("a2", b"pending")
        abort_backup("a2")
        delete_snapshot("a1")
        result = gc(0)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(gc_mod._state["blocks"], {})


class ResumeTest(unittest.TestCase):
    def test_interrupt_and_resume(self):
        store = tempfile.mkdtemp(prefix="snapshotgc-test-")
        reset(store)
        _backup("a1", blocks=[b"x", b"y", b"z"])
        delete_snapshot("a1")

        calls = []
        real_remove = gc_mod._remove_block_file

        def flaky_remove(bid):
            calls.append(bid)
            if len(calls) == 2:
                raise OSError("simulated crash")
            return real_remove(bid)

        with mock.patch.object(gc_mod, "_remove_block_file", flaky_remove):
            self.assertRaises(OSError, gc, 0)

        reset(store)  # 模拟重启，从剩余标记续跑
        result = gc(0)
        self.assertEqual(result["removed"], 2)
        self.assertEqual(result["freed"], 2)
        self.assertEqual(gc_mod._state["blocks"], {})
        self.assertEqual(gc_mod._state["gc_pending"], [])
