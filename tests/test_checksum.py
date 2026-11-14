import os
import shutil
import tempfile
import unittest

from lakechecksum import audit, drop_partition, repair, track, verify

_STATE_DIR = None


def setUpModule():
    global _STATE_DIR
    _STATE_DIR = tempfile.mkdtemp()
    os.environ["LAKECHECKSUM_HOME"] = _STATE_DIR
    os.environ["LAKECHECKSUM_BLOCK_SIZE"] = "4"


def tearDownModule():
    shutil.rmtree(_STATE_DIR, ignore_errors=True)
    os.environ.pop("LAKECHECKSUM_HOME", None)
    os.environ.pop("LAKECHECKSUM_BLOCK_SIZE", None)


def _wipe(partition):
    shutil.rmtree(os.path.join(_STATE_DIR, "partitions", partition),
                  ignore_errors=True)


class ChecksumTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "a.bin")
        open(self.path, "wb").write(b"abc")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_sha_length(self):
        self.assertEqual(len(verify(self.path)["sha256"]), 64)

    def test_single_block(self):
        self.assertEqual(verify(self.path)["blocks"], 1)

    def test_repair_zero(self):
        self.assertEqual(repair("p1")["repaired"], 0)


class ChunkedVerifyTest(unittest.TestCase):
    """分块校验：固定块、偏移记录、只重算变化块。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "chunked.bin")
        with open(self.path, "wb") as handle:
            handle.write(b"0123456789")  # 块大小 4 -> 3 块

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_block_count_and_offsets(self):
        result = verify(self.path)
        self.assertEqual(result["blocks"], 3)
        self.assertEqual(result["block_size"], 4)

    def test_only_changed_blocks_reported(self):
        verify(self.path)
        with open(self.path, "r+b") as handle:
            handle.seek(5)
            handle.write(b"X")  # 落在第 1 块
        result = verify(self.path)
        self.assertEqual(result["changed"], [1])
        self.assertEqual(result["block_status"], ["ok", "changed", "ok"])

    def test_unchanged_file_uses_cache(self):
        first = verify(self.path)
        second = verify(self.path)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["recomputed"], 0)
        self.assertEqual(first["sha256"], second["sha256"])


class RepairTest(unittest.TestCase):
    """坏块从副本重取，修复后更新清单并写审计。"""

    def setUp(self):
        _wipe("repair-p")
        self.dir = tempfile.mkdtemp()
        self.lake = os.path.join(self.dir, "lake.bin")
        self.replica = os.path.join(self.dir, "replica.bin")
        self.data = b"0123456789ABCDEF"
        for path in (self.lake, self.replica):
            with open(path, "wb") as handle:
                handle.write(self.data)
        track("repair-p", self.lake, replica=self.replica)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        drop_partition("repair-p")

    def _corrupt(self, offset, payload):
        with open(self.lake, "r+b") as handle:
            handle.seek(offset)
            handle.write(payload)

    def test_bad_block_repaired_from_replica(self):
        self._corrupt(5, b"XX")  # 第 1 块
        result = repair("repair-p")
        self.assertEqual(result["repaired"], 1)
        self.assertEqual(result["remaining_bad"], 0)
        with open(self.lake, "rb") as handle:
            self.assertEqual(handle.read(), self.data)

    def test_manifest_updated_after_repair(self):
        self._corrupt(9, b"YY")  # 第 2 块
        result = repair("repair-p")
        self.assertEqual(result["manifest_diff"]["updated"], [self.lake])
        self.assertEqual(verify(self.lake)["changed"], [])  # 缓存已同步

    def test_audit_records_before_and_after(self):
        self._corrupt(1, b"Z")
        repair("repair-p")
        entries = [e for e in audit("repair-p") if e["action"] == "repair"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["repaired_blocks"], 1)
        self.assertEqual(len(entry["before_sha256"]), 64)
        self.assertEqual(len(entry["after_sha256"]), 64)
        self.assertNotEqual(entry["before_sha256"], entry["after_sha256"])

    def test_repair_idempotent(self):
        self._corrupt(5, b"XX")
        first = repair("repair-p")
        second = repair("repair-p")
        self.assertEqual(first["repaired"], 1)
        self.assertEqual(second["repaired"], 0)
        self.assertEqual(second["remaining_bad"], 0)

    def test_unrepairable_block_reported(self):
        self._corrupt(5, b"XX")
        os.remove(self.replica)
        result = repair("repair-p")
        self.assertEqual(result["repaired"], 0)
        self.assertEqual(result["remaining_bad"], 1)
        self.assertEqual(result["bad_blocks"][0]["block"], 1)


class ManifestSyncTest(unittest.TestCase):
    """清单同步：track 登记、drop_partition 清理。"""

    def setUp(self):
        _wipe("sync-p")
        _wipe("other-p")
        self.dir = tempfile.mkdtemp()
        self.paths = []
        for name in ("a.bin", "b.bin"):
            path = os.path.join(self.dir, name)
            with open(path, "wb") as handle:
                handle.write(b"data-" + name.encode())
            self.paths.append(path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        drop_partition("sync-p")

    def test_track_adds_and_updates(self):
        first = track("sync-p", self.paths[0])
        self.assertEqual(first["manifest_diff"]["added"], [self.paths[0]])
        second = track("sync-p", self.paths[0])
        self.assertEqual(second["manifest_diff"]["updated"], [self.paths[0]])

    def test_drop_partition_cleans_manifest(self):
        for path in self.paths:
            track("sync-p", path)
        result = drop_partition("sync-p")
        self.assertEqual(result["removed"], sorted(self.paths))
        # 清单已清理，修复无动作
        self.assertEqual(repair("sync-p")["repaired"], 0)
        # 审计仍可按分区查询
        actions = [e["action"] for e in audit("sync-p")]
        self.assertIn("drop_partition", actions)

    def test_audit_query_by_partition(self):
        track("sync-p", self.paths[0])
        track("other-p", self.paths[1])
        sync_entries = audit("sync-p")
        self.assertTrue(all(e["partition"] == "sync-p" for e in sync_entries))
        self.assertEqual(len(audit("other-p")), 1)
        drop_partition("other-p")


class IdempotencyTest(unittest.TestCase):
    """幂等：重复 verify/track/drop 结果稳定。"""

    def setUp(self):
        _wipe("idem-p")
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "idem.bin")
        with open(self.path, "wb") as handle:
            handle.write(b"0123456789")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        drop_partition("idem-p")

    def test_verify_idempotent(self):
        self.assertEqual(verify(self.path)["sha256"], verify(self.path)["sha256"])

    def test_track_idempotent(self):
        track("idem-p", self.path)
        track("idem-p", self.path)
        self.assertEqual(len([e for e in audit("idem-p") if e["action"] == "track"]), 2)
        self.assertEqual(verify(self.path)["changed"], [])

    def test_drop_idempotent(self):
        track("idem-p", self.path)
        first = drop_partition("idem-p")
        second = drop_partition("idem-p")
        self.assertEqual(first["removed"], [self.path])
        self.assertEqual(second["removed"], [])
